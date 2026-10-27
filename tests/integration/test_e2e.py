"""Uçdan-uca ssenari (spec 13-cü bölmə), HTTP səviyyəsində: iki bot, 8 agent (saxta LLM), outbox, iş növbəsi.

sual → müraciət → triaj → təyinat → AI layihəsi → vətəndaşa sual → reply → cavab → QA → təsdiq → çatdırılma
→ 👎 → yenidən açılma. Privacy: bütün ssenari boyu LLM-lərə gedən mətnlərdə vətəndaşın FİN-i, telefonu və
ünvanı olmamalıdır (vətəndaş onları mesajlarında bilərəkdən yazır). LLM-siz rejimdə də müraciət çatdırılır.
"""
import itertools
from datetime import datetime
from functools import partial

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.agent_jobs import AgentJobs, AgentSet
from app.agents.citizen_graph import BTN_START_APPEAL, CitizenAgents, build_citizen_graph, run_turn
from app.agents.drafter import DrafterAgent
from app.agents.faq import FaqAgent
from app.agents.guard import GuardAgent
from app.agents.intake import IntakeAgent
from app.agents.qa import QaAgent
from app.agents.router import RouterAgent
from app.agents.routing import RoutingAgent
from app.agents.triage import TriageAgent
from app.api import create_app
from app.api.deps import get_citizen_controller, get_staff_controller
from app.citizen.controller import APPEAL_SEND, PROFILE_YES, CitizenController
from app.citizen.session import MemoryLimiter, MemorySessionStore
from app.db.tables import Appeal, TelegramMessage
from app.jobs import JobRunner
from app.knowledge.kb import KnowledgeHit
from app.notifier.cards import ANSWER, APPROVE, ASK, ASSIGN, FEEDBACK_NEG, callback
from app.notifier.notifier import CITIZEN_QUESTION
from app.notifier.outbox import OutboxWorker
from app.privacy.llm import PrivateLLM
from app.staff.controller import OUTCOME, SEND, StaffController
from app.staff.session import StaffSession
from app.workflow.states import AppealStatus, BotKind, Outcome
from tests.fakes import FakeChatModel, FakeTelegramClient
from tests.integration.helpers import (
    BAKU, CAL, CFG, CITIZEN_TG, COORDINATOR_TG, CRYPTO, ORG, STAFF_A_TG,
)

pytestmark = pytest.mark.integration
TOKEN = "e2e-service-token"
HIT = KnowledgeHit(1, "Pensiya, sosial müavinət və təqaüd ödənişləri", "02.md",
                   "Bakıda pensiyalar hər ayın 8-15-i aralığında ödənilir.", 0.2)
# helpers.complete_citizen: FİN 5ABC12D, telefon +994501234567, ünvan "Nizami küç. 5, m. 12"
SECRETS = ("5ABC12D", "+994501234567", "0501234567", "050 123 45 67", "Nizami küç", "Hüseynova", "Nigar")
AGENTS = ("guard", "router", "faq", "intake", "triage", "routing", "drafter", "qa")


class System:
    def __init__(self, app_engine, monkeypatch, scripts: dict[str, list]):
        monkeypatch.setattr("app.api.deps.settings.service_token", TOKEN)
        clock = lambda: datetime.now(BAKU)  # noqa: E731 (iş növbəsi next_attempt_at-ı bazanın real saatı ilə yazır)
        self.factory = sessionmaker(app_engine, expire_on_commit=False)
        self.models = {name: FakeChatModel(script=list(scripts.get(name, []))) for name in AGENTS}
        llm = {name: PrivateLLM(name, model=model) for name, model in self.models.items()}
        search = lambda query: [HIT]  # noqa: E731

        graph = build_citizen_graph(
            CitizenAgents(GuardAgent(llm["guard"]), RouterAgent(llm["router"]), FaqAgent(llm["faq"]),
                          IntakeAgent(llm["intake"])),
            search=search, templates=CFG.templates, org_name=ORG, hotline="", checkpointer=MemorySaver())
        self.citizen_tg = FakeTelegramClient(first_message_id=5000)
        self.staff_tg = FakeTelegramClient(first_message_id=9000)
        citizen = CitizenController(
            self.factory, store=MemorySessionStore(), limiter=MemoryLimiter(), turn=partial(run_turn, graph),
            client=self.citizen_tg, config=CFG, calendar=CAL, crypto=CRYPTO, org_name=ORG, hotline="",
            rate_limit=100, clock=clock)
        qa = QaAgent(llm["qa"])
        staff = StaffController(
            self.factory, store=MemorySessionStore(StaffSession), limiter=MemoryLimiter(), client=self.staff_tg,
            config=CFG, calendar=CAL, crypto=CRYPTO, org_name=ORG,
            qa=lambda **kwargs: qa.review(**kwargs, search=search), bot_username="e2e_staff_bot", clock=clock)
        jobs = AgentJobs(self.factory, config=CFG, calendar=CAL, crypto=CRYPTO, org_name=ORG, search=search,
                         agents=AgentSet(TriageAgent(llm["triage"]), RoutingAgent(llm["routing"]),
                                         DrafterAgent(llm["drafter"])), clock=clock)
        self.jobs = JobRunner(self.factory, jobs.handlers(), retry_delays=CFG.sla.outbox.retry_delays_seconds,
                              stale_minutes=10, clock=clock)
        self.worker = OutboxWorker(self.factory, {BotKind.citizen: self.citizen_tg, BotKind.staff: self.staff_tg},
                                   config=CFG.sla.outbox, clock=clock)
        app = create_app()
        app.dependency_overrides[get_citizen_controller] = lambda: citizen
        app.dependency_overrides[get_staff_controller] = lambda: staff
        self.http = TestClient(app)
        self._ids = itertools.count(1)

    # ---------- Telegram tərəfi: runner-in göndərdiyi update-lər ----------
    def _post(self, kind: str, update: dict) -> None:
        response = self.http.post(f"/bot/{kind}/update", json=update, headers={"X-Service-Token": TOKEN})
        assert response.json() == {"ok": True}, response.text
        while self.jobs.process_batch() or self.worker.process_batch():
            pass

    def say(self, kind: str, user: int, text: str, reply_to: int | None = None) -> None:
        n = next(self._ids)
        message = {"message_id": n, "from": {"id": user, "first_name": "Test"},
                   "chat": {"id": user, "type": "private"}, "date": 0, "text": text}
        if reply_to:
            message["reply_to_message"] = {"message_id": reply_to}
        self._post(kind, {"update_id": n, "message": message})

    def press(self, kind: str, user: int, data: str) -> None:
        n = next(self._ids)
        self._post(kind, {"update_id": n, "callback_query": {
            "id": f"cb{n}", "from": {"id": user}, "data": data,
            "message": {"message_id": 1, "chat": {"id": user, "type": "private"}}}})

    def texts(self, client: FakeTelegramClient, chat: int) -> list[str]:
        return [p["text"] for m, c, p in client.sent if m == "send_message" and c == chat]

    def appeal(self) -> Appeal:
        with self.factory() as s:
            return s.scalars(select(Appeal).order_by(Appeal.id.desc())).first()

    def llm_text(self) -> str:
        return "\n".join(model.all_text() for model in self.models.values())


def register_appeal(system: System, text: str) -> Appeal:
    system.say("citizen", CITIZEN_TG, text)
    system.press("citizen", CITIZEN_TG, PROFILE_YES)  # qayıdan vətəndaş: profil təsdiqi
    system.press("citizen", CITIZEN_TG, APPEAL_SEND)
    return system.appeal()


def test_full_cycle_with_privacy_spy(app_engine, world, monkeypatch):
    tool_call = AIMessage("", tool_calls=[{"name": "search_knowledge_base", "args": {"query": "pensiya ödənişi"},
                                           "id": "call_1"}])
    system = System(app_engine, monkeypatch, {
        "router": [{"intent": "question", "reason": "qrafik"}, {"intent": "new_appeal", "reason": "problem"}],
        "faq": [{"answerable": True, "answer": "Bakıda pensiya hər ayın 8-15-i aralığında ödənilir.",
                 "source_ids": [1]}],
        "intake": [{"complete": True, "next_question": None, "subject": "Pensiya köçmür",
                    "summary": "Vətəndaşın pensiyası 3 aydır köçmür, ünvan [UNVAN_1]"}],
        "triage": [{"category": "complaint", "priority": "high", "legal_issue": False, "reason": "ödəniş"}],
        "routing": [{"department_code": "pensiya", "reason": "pensiya ödənişi"}],
        "drafter": [tool_call, AIMessage("Kifayətdir"),
                    {"text": "Hörmətli vətəndaş, pensiya ödənişiniz yoxlanılır.", "source_ids": [1],
                     "missing_info": ["ödənişin bərpa tarixi"]}],
        "qa": [{"issues": []}],
    })

    # 1. Sual → bilik bazasından cavab
    system.say("citizen", CITIZEN_TG, "Pensiya ayın neçəsində köçürülür?")
    assert "8-15" in system.texts(system.citizen_tg, CITIZEN_TG)[-1]

    # 2. Müraciət: vətəndaş FİN-ini, telefonunu və ünvanını mətndə yazır
    appeal = register_appeal(system, "Pensiyam 3 aydır köçmür. FİN 5ABC12D, telefon 050 123 45 67, "
                                     "ünvan Nizami küç. 5, m. 12. Qonşum Hüseynova Nigar da təsdiqləyir")
    assert appeal.status == AppealStatus.new and appeal.department_id is not None
    assert any(appeal.number in t for t in system.texts(system.staff_tg, COORDINATOR_TG))  # triaj → kart

    # 3. Təyinat → icraçı kartı və AI layihəsi
    system.press("staff", COORDINATOR_TG, callback(ASSIGN, appeal.id, appeal.version, world.staff_a.id))
    assert any("AI cavab layihəsi" in t for t in system.texts(system.staff_tg, STAFF_A_TG))

    # 4. Vətəndaşa sual və reply ilə cavab
    appeal = system.appeal()
    system.press("staff", STAFF_A_TG, callback(ASK, appeal.id, appeal.version))
    system.say("staff", STAFF_A_TG, "Pensiya hansı banka köçürülürdü?")
    with system.factory() as s:
        question = s.scalar(select(TelegramMessage.message_id).where(TelegramMessage.purpose == CITIZEN_QUESTION))
    system.say("citizen", CITIZEN_TG, "Kapital Bank, kart 5ABC12D FİN-ə bağlıdır", reply_to=question)
    assert system.appeal().status == AppealStatus.assigned

    # 5. Cavab → QA → önizləmə → təsdiq (şikayət) → çatdırılma
    appeal = system.appeal()
    system.press("staff", STAFF_A_TG, callback(ANSWER, appeal.id, appeal.version))
    system.press("staff", STAFF_A_TG, callback(OUTCOME, appeal.id, appeal.version, Outcome.satisfied.value))
    system.say("staff", STAFF_A_TG, "Hörmətli vətəndaş, pensiyanız 24.10.2026 tarixində köçürüldü.")
    system.press("staff", STAFF_A_TG, callback(SEND, appeal.id, appeal.version))
    appeal = system.appeal()
    assert appeal.status == AppealStatus.pending_approval
    system.press("staff", COORDINATOR_TG, callback(APPROVE, appeal.id, appeal.version))
    assert system.appeal().status == AppealStatus.answered
    assert any("24.10.2026 tarixində köçürüldü" in t for t in system.texts(system.citizen_tg, CITIZEN_TG))

    # 6. 👎 → səbəb → yenidən açılır
    system.press("citizen", CITIZEN_TG, callback(FEEDBACK_NEG, appeal.id))
    system.say("citizen", CITIZEN_TG, "Pul hələ də gəlməyib")
    final = system.appeal()
    assert (final.status, final.reopen_count) == (AppealStatus.new, 1)

    # Privacy: bütün agentlər işlədi, heç birinə şəxsi məlumat getmədi
    assert all(system.models[name].received for name in AGENTS if name != "guard")
    sent = system.llm_text()
    assert [secret for secret in SECRETS if secret in sent] == []
    assert "[FIN_1]" in sent and "[UNVAN_1]" in sent and "[SEXS_1]" in sent  # maskalanmış formada çatıb


def test_appeal_is_delivered_when_every_llm_is_down(app_engine, world, monkeypatch):
    down = [RuntimeError("API əlçatan deyil")] * 20
    system = System(app_engine, monkeypatch, {name: down for name in AGENTS})

    system.say("citizen", CITIZEN_TG, "Pensiyam 3 aydır köçmür")
    assert system.texts(system.citizen_tg, CITIZEN_TG)[-1] == CFG.templates.texts["llm_unavailable"]
    system.press("citizen", CITIZEN_TG, BTN_START_APPEAL)  # qəbul agenti düşüb: vətəndaşın mətni ilə davam
    system.press("citizen", CITIZEN_TG, PROFILE_YES)
    system.press("citizen", CITIZEN_TG, APPEAL_SEND)
    appeal = system.appeal()
    assert appeal.triage_failed  # təhlükəsiz standart
    assert any("AI triajı alınmadı" in t for t in system.texts(system.staff_tg, COORDINATOR_TG))

    system.press("staff", COORDINATOR_TG, callback(ASSIGN, appeal.id, appeal.version, world.staff_a.id))
    assert any("hazırlana bilmədi" in t for t in system.texts(system.staff_tg, STAFF_A_TG))
    appeal = system.appeal()
    system.press("staff", STAFF_A_TG, callback(ANSWER, appeal.id, appeal.version))
    system.press("staff", STAFF_A_TG, callback(OUTCOME, appeal.id, appeal.version, Outcome.satisfied.value))
    system.say("staff", STAFF_A_TG, "Hörmətli vətəndaş, pensiyanız 24.10.2026 tarixində köçürüldü.")
    preview = system.texts(system.staff_tg, STAFF_A_TG)[-1]
    assert "AI keyfiyyət yoxlaması aparılmadı" in preview
    system.press("staff", STAFF_A_TG, callback(SEND, appeal.id, appeal.version))
    appeal = system.appeal()
    assert appeal.status == AppealStatus.pending_approval  # AI xətası → insan təsdiqi
    system.press("staff", COORDINATOR_TG, callback(APPROVE, appeal.id, appeal.version))
    assert system.appeal().status == AppealStatus.answered
    assert any("24.10.2026" in t for t in system.texts(system.citizen_tg, CITIZEN_TG))
