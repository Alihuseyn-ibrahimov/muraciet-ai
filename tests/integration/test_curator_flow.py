"""Kurator: 👍 alan cavab → agent təklifi → adminə kart → təsdiq → bilik bazası (saxta LLM, real PostgreSQL)."""
import itertools

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agent_jobs import AgentJobs, AgentSet
from app.agents.curator import CuratorAgent
from app.agents.drafter import DrafterAgent
from app.agents.routing import RoutingAgent
from app.agents.triage import TriageAgent
from app.citizen.session import MemoryLimiter, MemorySessionStore
from app.db.tables import Appeal, Job, KbEntry, Outbox
from app.jobs import JobSnapshot
from app.knowledge.kb import KnowledgeHit
from app.notifier.cards import KB_APPROVE, KB_REJECT, callback
from app.notifier.outbox import OutboxWorker
from app.privacy.llm import PrivateLLM
from app.staff.controller import StaffController
from app.staff.session import StaffSession
from app.workflow.service import AppealService, RoutingOutcome, TriageOutcome
from app.workflow.states import BotKind, Category, Outcome, Priority
from tests.fakes import FakeChatModel, FakeTelegramClient, RecordingNotifier
from tests.integration.helpers import CAL, CFG, COORDINATOR_TG, CRYPTO, DEPUTY_TG, MONDAY_10, ORG, Clock

pytestmark = pytest.mark.integration

PROPOSAL = {"suitable": True, "question": "Pensiya kartı itəndə nə etmək lazımdır?",
            "answer": "Bankın filialına şəxsiyyət vəsiqəsi ilə müraciət edib kartı bərpa etdirmək lazımdır.",
            "reason": "ümumi prosedur"}


def run(app_engine, fn):
    with Session(app_engine, expire_on_commit=False) as s, s.begin():
        return fn(AppealService(s, config=CFG, calendar=CAL, crypto=CRYPTO, notifier=RecordingNotifier(),
                                clock=Clock(MONDAY_10)))


def closed_with_thanks(app_engine, world) -> Appeal:
    appeal = run(app_engine, lambda svc: svc.create_appeal(
        world.citizen_id, subject="Pensiya kartı itib", summary="Kartım itib", description="Pensiya kartım itib"))
    appeal = run(app_engine, lambda svc: svc.apply_triage(
        appeal.id, TriageOutcome(Category.request, Priority.normal, False), RoutingOutcome("pensiya", "x")))
    appeal = run(app_engine, lambda svc: svc.assign(world.coordinator, appeal.id, world.staff_a.id,
                                                    expected_version=appeal.version))
    appeal = run(app_engine, lambda svc: svc.submit_answer(
        world.staff_a, appeal.id, outcome=Outcome.explained, qa_critical=False, expected_version=appeal.version,
        text="Hörmətli Əli Məmmədov, bankın filialına şəxsiyyət vəsiqəsi ilə müraciət edib kartı bərpa etdirin."))
    return run(app_engine, lambda svc: svc.citizen_feedback(world.citizen_id, appeal.id, satisfied=True))


def jobs(app_engine, *script) -> tuple[AgentJobs, FakeChatModel]:
    spy = FakeChatModel(script=list(script))
    agents = AgentSet(TriageAgent(PrivateLLM("triage", model=FakeChatModel())),
                      RoutingAgent(PrivateLLM("routing", model=FakeChatModel())),
                      DrafterAgent(PrivateLLM("drafter", model=FakeChatModel())),
                      CuratorAgent(PrivateLLM("curator", model=spy)))
    hit = KnowledgeHit(1, "Pensiya", "02.md", "Pensiya ayın 8-15-də köçürülür.", 0.3)
    return AgentJobs(sessionmaker(app_engine, expire_on_commit=False), config=CFG, calendar=CAL, crypto=CRYPTO,
                     org_name=ORG, search=lambda q: [hit], agents=agents, clock=Clock(MONDAY_10)), spy


def proposed(app_engine, world) -> KbEntry:
    appeal = closed_with_thanks(app_engine, world)
    agent_jobs, _ = jobs(app_engine, PROPOSAL)
    agent_jobs.curate(JobSnapshot(1, "curate", appeal.id, None))
    with Session(app_engine) as s:
        return s.scalar(select(KbEntry))


def test_positive_feedback_queues_a_curator_job(app_engine, world):
    appeal = closed_with_thanks(app_engine, world)
    with Session(app_engine) as s:
        assert s.scalar(select(Job.kind).where(Job.appeal_id == appeal.id, Job.kind == "curate")) == "curate"


def test_curator_proposal_goes_to_admins_without_personal_data(app_engine, world):
    appeal = closed_with_thanks(app_engine, world)
    agent_jobs, spy = jobs(app_engine, PROPOSAL)
    agent_jobs.curate(JobSnapshot(1, "curate", appeal.id, None))
    agent_jobs.curate(JobSnapshot(2, "curate", appeal.id, None))  # təkrar iş ikinci təklif yaratmır
    assert "Məmmədov" not in spy.all_text() and len(spy.received) == 1
    with Session(app_engine) as s:
        entry = s.scalar(select(KbEntry))
        cards = list(s.scalars(select(Outbox).where(Outbox.purpose == "kb_proposal")))
    assert (entry.status, entry.question, entry.appeal_id) == ("pending", PROPOSAL["question"], appeal.id)
    assert [c.chat_id for c in cards] == [COORDINATOR_TG]  # yalnız adminlər
    assert PROPOSAL["answer"] in cards[0].payload["text"]


def test_unsuitable_answer_creates_nothing(app_engine, world):
    appeal = closed_with_thanks(app_engine, world)
    agent_jobs, _ = jobs(app_engine, {"suitable": False, "reason": "fərdi qərar"})
    agent_jobs.curate(JobSnapshot(1, "curate", appeal.id, None))
    with Session(app_engine) as s:
        assert s.scalar(select(KbEntry)) is None


class Admin:
    def __init__(self, app_engine, kb_add=None):
        factory = sessionmaker(app_engine, expire_on_commit=False)
        self.factory, self.added = factory, []
        self.telegram = FakeTelegramClient()
        self.worker = OutboxWorker(factory, {BotKind.staff: self.telegram}, config=CFG.sla.outbox,
                                   clock=Clock(MONDAY_10))
        self.controller = StaffController(
            factory, store=MemorySessionStore(StaffSession), limiter=MemoryLimiter(), client=self.telegram,
            config=CFG, calendar=CAL, crypto=CRYPTO, org_name=ORG, qa=lambda **kw: None, bot_username="b",
            clock=Clock(MONDAY_10), kb_add=kb_add or (lambda *args: self.added.append(args)))
        self._ids = itertools.count(1)

    def press(self, data: str, user: int) -> str:
        n = next(self._ids)
        self.controller.handle({"update_id": n, "callback_query": {
            "id": f"cb{n}", "from": {"id": user}, "data": data,
            "message": {"message_id": 1, "chat": {"id": user, "type": "private"}}}})
        while self.worker.process_batch():
            pass
        return [p for m, _, p in self.telegram.sent if m == "answer_callback_query" and p["id"] == f"cb{n}"][0]["text"]

    def entry(self) -> KbEntry:
        with self.factory() as s:
            return s.scalar(select(KbEntry))


def test_admin_approval_adds_the_entry_to_the_knowledge_base(app_engine, world):
    entry = proposed(app_engine, world)
    admin = Admin(app_engine)
    assert "icazə" in admin.press(callback(KB_APPROVE, entry.id), DEPUTY_TG)  # admin deyil
    assert admin.added == [] and admin.entry().status == "pending"

    assert "əlavə olundu" in admin.press(callback(KB_APPROVE, entry.id), COORDINATOR_TG)
    assert admin.added == [(entry.id, PROPOSAL["question"], PROPOSAL["answer"])]
    assert (admin.entry().status, admin.entry().decided_by_staff_id) == ("approved", world.coordinator.id)
    assert "qərar verilib" in admin.press(callback(KB_REJECT, entry.id), COORDINATOR_TG)  # ikinci qərar yoxdur


def test_rejection_and_failed_write(app_engine, world):
    entry = proposed(app_engine, world)

    def broken(*args):
        raise ConnectionError("embedding servisi yoxdur")

    admin = Admin(app_engine, kb_add=broken)
    assert "yazılmadı" in admin.press(callback(KB_APPROVE, entry.id), COORDINATOR_TG)
    assert admin.entry().status == "pending"  # yenidən cəhd etmək olar
    assert "rədd" in admin.press(callback(KB_REJECT, entry.id), COORDINATOR_TG)
    assert admin.entry().status == "rejected"
