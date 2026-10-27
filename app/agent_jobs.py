"""Worker-in agent işləri (spec 5-ci bölmə): triaj (müraciət yarananda), cavab layihəsi (təyinatdan sonra) və
kurator (vətəndaş 👍 verəndə: bilik bazası üçün təklif, adminlərin təsdiqinə).

LLM çağırışı DB tranzaksiyasından kənardadır: əvvəlcə məlumat oxunur, sonra agentlər işləyir, nəticə
isə qısa bir tranzaksiyada yazılır. Saniyələrlə çəkən LLM çağırışı müddətində müraciət kilidlənmir.
"""
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agents.curator import CuratorAgent
from app.agents.drafter import DrafterAgent
from app.agents.routing import RoutingAgent
from app.agents.staff_pipeline import build_triage_graph, run_triage
from app.agents.triage import TriageAgent
from app.db.tables import Appeal, AppealEvent, AppealMessage, Citizen, KbEntry, Staff
from app.jobs import JobHandler, JobSnapshot
from app.knowledge.kb import KnowledgeHit
from app.notifier import cards, recipients
from app.notifier.notifier import OutboxNotifier
from app.notifier.outbox import enqueue_rendered
from app.privacy.crypto import Crypto
from app.privacy.profile import pseudonymizer_for
from app.workflow.calendar import WorkCalendar
from app.workflow.service import AppealService, RoutingOutcome, TriageOutcome
from app.workflow.states import ActorType, AppealStatus, BotKind, MessageKind
from app.yaml_config import AppConfig

DRAFTABLE = (AppealStatus.assigned, AppealStatus.waiting_citizen)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class AgentSet:
    triage: TriageAgent
    routing: RoutingAgent
    drafter: DrafterAgent
    curator: CuratorAgent = field(default_factory=CuratorAgent)

    @classmethod
    def default(cls) -> "AgentSet":
        return cls(TriageAgent(), RoutingAgent(), DrafterAgent())


class AgentJobs:
    def __init__(self, session_factory: sessionmaker[Session], *, config: AppConfig, calendar: WorkCalendar,
                 crypto: Crypto, org_name: str, search: Callable[[str], list[KnowledgeHit]],
                 agents: AgentSet | None = None, clock: Callable[[], datetime] = utc_now):
        self._sessions = session_factory
        self._config = config
        self._calendar = calendar
        self._crypto = crypto
        self._org_name = org_name
        self._search = search
        self._agents = agents or AgentSet.default()
        self._clock = clock
        self._triage_graph = build_triage_graph(self._agents.triage, self._agents.routing, config.departments)

    def handlers(self) -> dict[str, JobHandler]:
        return {"triage": self.triage, "draft": self.draft, "curate": self.curate}

    def _service(self, session: Session) -> AppealService:
        notifier = OutboxNotifier(session, config=self._config, crypto=self._crypto, org_name=self._org_name,
                                  clock=self._clock)
        return AppealService(session, config=self._config, calendar=self._calendar, crypto=self._crypto,
                             notifier=notifier, clock=self._clock)

    # ---------- triaj ----------
    def triage(self, job: JobSnapshot) -> None:
        with self._sessions() as s:
            appeal = s.get(Appeal, job.appeal_id)
            if appeal is None or appeal.category is not None:  # artıq triaj olunub
                return
            pseudo = pseudonymizer_for(s.get(Citizen, appeal.citizen_id), self._crypto)
            subject, summary, description = appeal.subject, appeal.summary, appeal.description

        triage, routing = run_triage(self._triage_graph, subject=subject, summary=summary, description=description,
                                     pseudo=pseudo)
        with self._sessions() as s, s.begin():
            self._service(s).apply_triage(
                job.appeal_id,
                TriageOutcome(triage.category, triage.priority, triage.legal_issue) if triage else None,
                RoutingOutcome(routing.department_code, routing.reason) if routing else None,
            )

    # ---------- cavab layihəsi ----------
    def draft(self, job: JobSnapshot) -> None:
        with self._sessions() as s:
            appeal = s.get(Appeal, job.appeal_id)
            if appeal is None or appeal.status not in DRAFTABLE or appeal.assigned_staff_id is None:
                return
            pseudo = pseudonymizer_for(s.get(Citizen, appeal.citizen_id), self._crypto)
            context = self._context(s, appeal)

        draft = self._agents.drafter.draft(context, pseudo, search=self._search)
        with self._sessions() as s, s.begin():
            appeal = s.get(Appeal, job.appeal_id)
            staff = s.get(Staff, appeal.assigned_staff_id) if appeal.assigned_staff_id else None
            if staff is None or not staff.is_active:
                return
            now = self._clock()
            if draft is None:
                enqueue_rendered(s, bot=BotKind.staff, chat_id=staff.telegram_user_id, appeal_id=appeal.id,
                                 purpose="staff_info", now=now, rendered=cards.staff_info(
                                     f"🤖 {appeal.number}: AI cavab layihəsi hazırlana bilmədi. Cavabı özünüz yazın."))
                return
            message = AppealMessage(appeal_id=appeal.id, kind=MessageKind.ai_draft, text=draft.text, created_at=now,
                                    meta={"source_ids": draft.source_ids, "missing_info": draft.missing_info})
            s.add(message)
            s.flush()
            s.add(AppealEvent(appeal_id=appeal.id, actor_type=ActorType.ai, action="ai_draft_created",
                              from_status=appeal.status, to_status=appeal.status, created_at=now,
                              payload={"message_id": message.id}))
            enqueue_rendered(s, bot=BotKind.staff, chat_id=staff.telegram_user_id, appeal_id=appeal.id,
                             purpose="ai_draft", now=now, rendered=cards.draft_message(
                                 appeal, text=draft.text, missing_info=draft.missing_info,
                                 draft_message_id=message.id))

    # ---------- kurator ----------
    def curate(self, job: JobSnapshot) -> None:
        with self._sessions() as s:
            appeal = s.get(Appeal, job.appeal_id)
            if appeal is None or appeal.satisfaction != 1 \
                    or s.scalar(select(KbEntry.id).where(KbEntry.appeal_id == appeal.id)) is not None:
                return
            answer = s.scalar(select(AppealMessage.text)
                              .where(AppealMessage.appeal_id == appeal.id,
                                     AppealMessage.kind == MessageKind.final_answer)
                              .order_by(AppealMessage.id.desc()).limit(1))
            if not answer:
                return
            pseudo = pseudonymizer_for(s.get(Citizen, appeal.citizen_id), self._crypto)
            subject, summary, number = appeal.subject, appeal.summary, appeal.number

        proposal = self._agents.curator.propose(subject=subject, summary=summary, answer=answer, pseudo=pseudo,
                                                search=self._search)
        if proposal is None:
            return
        with self._sessions() as s, s.begin():
            entry = KbEntry(appeal_id=job.appeal_id, question=proposal.question, answer=proposal.answer,
                            created_at=self._clock())
            s.add(entry)
            s.flush()
            for admin in recipients.admins(s):
                enqueue_rendered(s, bot=BotKind.staff, chat_id=admin.telegram_user_id, appeal_id=job.appeal_id,
                                 purpose=cards.KB_PROPOSAL, now=self._clock(),
                                 rendered=cards.kb_proposal(entry, number=number))

    @staticmethod
    def _context(session: Session, appeal: Appeal) -> str:
        lines = [f"Mövzu: {appeal.subject}", f"Xülasə: {appeal.summary}", f"Təsvir: {appeal.description}"]
        labels = {MessageKind.staff_question: "Əməkdaşın sualı", MessageKind.citizen_reply: "Vətəndaşın cavabı"}
        for message in session.scalars(
            select(AppealMessage)
            .where(AppealMessage.appeal_id == appeal.id, AppealMessage.kind.in_(list(labels)))
            .order_by(AppealMessage.id)
        ):
            lines.append(f"{labels[message.kind]}: {message.text}")
        return "\n".join(lines)
