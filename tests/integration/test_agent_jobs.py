"""Worker-in agent işləri: triaj və cavab layihəsi (saxta LLM, real PostgreSQL)."""
import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agent_jobs import AgentJobs, AgentSet
from app.agents.drafter import DrafterAgent
from app.agents.routing import RoutingAgent
from app.agents.triage import TriageAgent
from app.db.tables import Appeal, AppealEvent, AppealMessage, Department, Outbox
from app.jobs import JobSnapshot
from app.knowledge.kb import KnowledgeHit
from app.privacy.llm import PrivateLLM
from app.workflow.service import AppealService
from app.workflow.states import AppealStatus, Category, MessageKind
from tests.fakes import FakeChatModel, RecordingNotifier
from tests.integration.helpers import CAL, CFG, CRYPTO, MONDAY_10, ORG, STAFF_A_TG, Clock

pytestmark = pytest.mark.integration

HIT = KnowledgeHit(1, "Xidmətlər", "02.md", "Küçə işıqları 3 iş günü ərzində bərpa olunur.", 0.2)
PII = ["Məmmədov", "5ABC12D", "0501234567", "Nizami küç"]
TRIAGE_OK = {"category": "complaint", "priority": "normal", "legal_issue": False, "reason": "narazılıq"}
ROUTING_OK = {"department_code": "pensiya", "reason": "pensiya ödənişi"}


class Spies:
    def __init__(self, triage=(), routing=(), drafter=()):
        self.triage = FakeChatModel(script=list(triage))
        self.routing = FakeChatModel(script=list(routing))
        self.drafter = FakeChatModel(script=list(drafter))

    def jobs(self, app_engine) -> AgentJobs:
        agents = AgentSet(TriageAgent(PrivateLLM("triage", model=self.triage)),
                          RoutingAgent(PrivateLLM("routing", model=self.routing)),
                          DrafterAgent(PrivateLLM("drafter", model=self.drafter)))
        return AgentJobs(sessionmaker(app_engine, expire_on_commit=False), config=CFG, calendar=CAL, crypto=CRYPTO,
                         org_name=ORG, search=lambda q: [HIT], agents=agents, clock=Clock(MONDAY_10))

    def all_text(self) -> str:
        return self.triage.all_text() + self.routing.all_text() + self.drafter.all_text()


def service_run(app_engine, fn):
    with Session(app_engine, expire_on_commit=False) as s, s.begin():
        return fn(AppealService(s, config=CFG, calendar=CAL, crypto=CRYPTO, notifier=RecordingNotifier(),
                                clock=Clock(MONDAY_10)))


def created(app_engine, world) -> Appeal:
    return service_run(app_engine, lambda svc: svc.create_appeal(
        world.citizen_id, subject="Küçə işığı",
        summary="Əli Məmmədov: Nizami küç. 5, m. 12 yanında işıqlar yanmır",
        description="Mən Əli Məmmədov (FİN 5ABC12D, tel 0501234567). Nizami küç. 5, m. 12 yanında işıqlar yanmır."))


def job(kind: str, appeal: Appeal) -> JobSnapshot:
    return JobSnapshot(1, kind, appeal.id, None)


def get(app_engine, appeal_id) -> Appeal:
    with Session(app_engine) as s:
        return s.get(Appeal, appeal_id)


def outbox(app_engine) -> list[Outbox]:
    with Session(app_engine) as s:
        return list(s.scalars(select(Outbox).order_by(Outbox.id)))


# ---------- triaj ----------
def test_triage_job_applies_results_and_notifies_coordinators_without_leaking_pii(app_engine, world):
    appeal = created(app_engine, world)
    spies = Spies(triage=[TRIAGE_OK], routing=[ROUTING_OK])
    spies.jobs(app_engine).triage(job("triage", appeal))
    appeal = get(app_engine, appeal.id)
    assert appeal.category == Category.complaint and not appeal.triage_failed
    assert len([r for r in outbox(app_engine) if r.purpose == "coordinator_card"]) == 2
    assert not [p for p in PII if p in spies.all_text()]


def test_triage_job_failure_falls_back_to_general(app_engine, world):
    appeal = created(app_engine, world)
    Spies(triage=[RuntimeError("API")], routing=[RuntimeError("API")]).jobs(app_engine).triage(job("triage", appeal))
    appeal = get(app_engine, appeal.id)
    with Session(app_engine) as s:
        assert appeal.triage_failed and s.get(Department, appeal.department_id).code == "general"


def test_triage_job_runs_only_once(app_engine, world):
    appeal = created(app_engine, world)
    Spies(triage=[TRIAGE_OK], routing=[ROUTING_OK]).jobs(app_engine).triage(job("triage", appeal))
    again = Spies()  # ssenari boşdur: LLM çağırılsa AssertionError olardı
    again.jobs(app_engine).triage(job("triage", appeal))
    assert again.triage.received == [] and again.routing.received == []


# ---------- cavab layihəsi ----------
def assigned(app_engine, world) -> Appeal:
    appeal = created(app_engine, world)
    Spies(triage=[TRIAGE_OK], routing=[ROUTING_OK]).jobs(app_engine).triage(job("triage", appeal))
    appeal = get(app_engine, appeal.id)
    return service_run(app_engine, lambda svc: svc.assign(world.coordinator, appeal.id, world.staff_a.id,
                                                          expected_version=appeal.version))


def test_draft_job_stores_draft_and_sends_it_to_assignee(app_engine, world):
    appeal = assigned(app_engine, world)
    spies = Spies(drafter=[AIMessage("Kifayətdir"),
                           {"text": "Hörmətli vətəndaş, işıqlar bərpa olunacaq.", "source_ids": [1],
                            "missing_info": ["bərpa tarixi"]}])
    spies.jobs(app_engine).draft(job("draft", appeal))
    with Session(app_engine) as s:
        draft = s.scalar(select(AppealMessage).where(AppealMessage.kind == MessageKind.ai_draft))
        assert draft.text.startswith("Hörmətli vətəndaş") and draft.meta["missing_info"] == ["bərpa tarixi"]
        assert s.scalar(select(AppealEvent.action).where(AppealEvent.action == "ai_draft_created"))
    message = next(r for r in outbox(app_engine) if r.purpose == "ai_draft")
    assert message.chat_id == STAFF_A_TG and "bərpa tarixi" in message.payload["text"]
    buttons = message.payload["reply_markup"]["inline_keyboard"][0]
    assert buttons[0]["callback_data"] == f"ud:{appeal.id}:{appeal.version}:{draft.id}"
    assert not [p for p in PII if p in spies.all_text()]


def test_draft_failure_tells_the_assignee(app_engine, world):
    appeal = assigned(app_engine, world)
    Spies(drafter=[RuntimeError("API"), RuntimeError("API")]).jobs(app_engine).draft(job("draft", appeal))
    info = next(r for r in outbox(app_engine) if r.purpose == "staff_info")
    assert info.chat_id == STAFF_A_TG and "hazırlana bilmədi" in info.payload["text"]


def test_draft_job_skips_appeal_that_is_no_longer_assigned(app_engine, world):
    appeal = assigned(app_engine, world)
    service_run(app_engine, lambda svc: svc.return_to_coordinator(world.staff_a, appeal.id, "Mənə aid deyil",
                                                                  expected_version=appeal.version))
    spies = Spies()
    spies.jobs(app_engine).draft(job("draft", appeal))
    assert spies.drafter.received == [] and get(app_engine, appeal.id).status == AppealStatus.new
