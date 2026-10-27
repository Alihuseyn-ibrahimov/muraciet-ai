"""Admin API (MCP onu Claude Desktop-dan çağırır): yalnız oxuma, şəxsi məlumat maskalanır."""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app.api import create_app
from app.api.deps import get_crypto_dep, get_session_factory_dep
from app.workflow.service import AppealService, RoutingOutcome, TriageOutcome
from app.workflow.states import Category, Priority
from tests.fakes import RecordingNotifier
from tests.integration.helpers import BAKU, CAL, CFG, CRYPTO, Clock

pytestmark = pytest.mark.integration
KEY = "admin-test-key-0001"
NOW = datetime.now(BAKU)  # admin API "bu gün"-ü real saatdan götürür


@pytest.fixture
def client(app_engine, monkeypatch):
    monkeypatch.setattr("app.api.deps.settings.admin_api_key", KEY)
    app = create_app()
    factory = sessionmaker(app_engine, expire_on_commit=False)
    app.dependency_overrides[get_session_factory_dep] = lambda: factory
    app.dependency_overrides[get_crypto_dep] = lambda: CRYPTO
    return TestClient(app)


def make_appeal(app_engine, world, *, when=NOW, summary="Məmmədov Əli, FİN 5ABC12D, pensiyam köçmür"):
    with Session(app_engine, expire_on_commit=False) as s, s.begin():
        svc = AppealService(s, config=CFG, calendar=CAL, crypto=CRYPTO, notifier=RecordingNotifier(),
                            clock=Clock(when))
        appeal = svc.create_appeal(world.citizen_id, subject="Pensiya köçmür", summary=summary,
                                   description="Nizami küç. 5, m. 12-də yaşayıram")
        return svc.apply_triage(appeal.id, TriageOutcome(Category.complaint, Priority.high, False),
                                RoutingOutcome("pensiya", "pensiya"))


def get(client, path, **params):
    return client.get(path, params=params, headers={"X-API-Key": KEY})


def test_admin_api_requires_the_key(client, monkeypatch):
    assert client.get("/admin/stats").status_code == 401
    assert client.get("/admin/stats", headers={"X-API-Key": "wrong"}).status_code == 401
    monkeypatch.setattr("app.api.deps.settings.admin_api_key", "")
    assert client.get("/admin/stats", headers={"X-API-Key": ""}).status_code == 401  # açar yoxdursa bağlıdır


def test_list_and_stats_contain_only_metadata(client, app_engine, world):
    appeal = make_appeal(app_engine, world)
    rows = get(client, "/admin/appeals").json()
    assert rows == [{"number": appeal.number, "status": "new", "category": "complaint", "priority": "high",
                     "department": "pensiya", "due_date": appeal.due_date.isoformat(),
                     "created_at": rows[0]["created_at"], "assignee": None}]
    assert get(client, "/admin/appeals", status="closed").json() == []
    stats = get(client, "/admin/stats").json()
    assert stats["by_status"] == {"new": 1} and stats["overdue"] == 0


def test_details_are_masked(client, app_engine, world):
    appeal = make_appeal(app_engine, world)
    body = get(client, f"/admin/appeals/{appeal.number}").json()
    text = str(body)
    for secret in ("Məmmədov", "5ABC12D", "Nizami küç"):
        assert secret not in text, secret
    assert "[SOYAD_1]" in body["summary"] and "pensiyam köçmür" in body["summary"]
    assert [e["action"] for e in body["events"]] == ["create", "apply_triage"]
    assert get(client, "/admin/appeals/MR-2026-999999").status_code == 404


def test_overdue_list(client, app_engine, world):
    old = make_appeal(app_engine, world, when=NOW - timedelta(days=40))
    make_appeal(app_engine, world)
    assert [r["number"] for r in get(client, "/admin/appeals/overdue").json()] == [old.number]


# ---------- bilik bazasının boşluqları ----------
def chat(app_engine, *rows, when=NOW):
    from app.db.tables import ChatLog

    with Session(app_engine) as s, s.begin():
        for tg, role, content, intent in rows:
            s.add(ChatLog(citizen_telegram_id=tg, role=role, content=content, intent=intent, created_at=when))


def test_knowledge_gaps_list_unanswered_questions_masked_and_counted(client, app_engine):
    no_answer = CFG.templates.render("no_answer_offer_appeal")
    chat(app_engine,
         (7001, "citizen", "Avtomobil vergisi nə qədərdir?", "question"), (7001, "bot", no_answer, "question"),
         (7002, "citizen", "avtomobil vergisi nə qədərdir", "question"), (7002, "bot", no_answer, "question"),
         (7003, "citizen", "Qonşum Hüseynova Nigar, tel 050 123 45 67, ona kömək necə alınır?", "question"),
         (7003, "bot", no_answer, "question"),
         (7004, "citizen", "Pensiya nə vaxt köçür?", "question"), (7004, "bot", "Ayın 8-15-i. Mənbə: ...", "question"),
         (7005, "citizen", "Pensiya artımı?", "question"), (7005, "bot", CFG.templates.render("llm_unavailable"),
                                                           "question"))
    chat(app_engine, (7006, "citizen", "Köhnə sual", "question"), (7006, "bot", no_answer, "question"),
         when=NOW - timedelta(days=60))

    gaps = get(client, "/admin/knowledge-gaps", days=30).json()
    assert gaps[0] == {"question": "Avtomobil vergisi nə qədərdir?", "count": 2}
    assert len(gaps) == 2  # cavablanan, LLM xətası və köhnə sual daxil deyil
    assert "Hüseynova" not in str(gaps) and "123 45 67" not in str(gaps) and "[SEXS_1]" in str(gaps)


def test_weekly_report_contains_only_aggregates(client, app_engine, world, monkeypatch):
    monkeypatch.setattr("app.agents.analyst.AnalystAgent.analyse", lambda self, stats, pseudo: None)  # LLM-siz
    make_appeal(app_engine, world)
    assert client.get("/admin/report").status_code == 401
    body = get(client, "/admin/report", days=7).json()
    assert body["stats"]["created"] == 1 and body["ai"] is False and "Daxil olub: 1" in body["text"]
    assert "Məmmədov" not in str(body) and "5ABC12D" not in str(body)


# ---------- MCP-nin yazma əməliyyatı: yalnız təklif, koordinator təsdiqləyir ----------
def post(client, path, body):
    return client.post(path, json=body, headers={"X-API-Key": KEY})


def test_staff_list_has_no_contact_data(client, world):
    from app.workflow.states import StaffRole

    people = get(client, "/admin/staff").json()
    assert {p["name"] for p in people} >= {"Rəşad Hüseynov", "Leyla Məmmədova"}
    assert all(set(p) == {"id", "name", "role", "department", "open"} for p in people)
    assert StaffRole.head.value not in {p["role"] for p in people}  # rəhbərə müraciət təyin olunmur


def test_assignment_proposal_only_sends_a_card_and_changes_nothing(client, app_engine, world):
    from app.db.tables import AppealEvent, Outbox
    from app.notifier.cards import ASSIGN, callback
    from tests.integration.helpers import COORDINATOR_TG, DEPUTY_TG

    appeal = make_appeal(app_engine, world)
    assert client.post("/admin/proposals/assign", json={}).status_code == 401
    answer = post(client, "/admin/proposals/assign",
                  {"number": appeal.number, "staff_id": world.staff_a.id, "reason": "pensiya üzrə təcrübəlidir"})
    assert answer.status_code == 200 and answer.json() == {"sent_to": 2}
    with Session(app_engine) as s:
        fresh = s.get(type(appeal), appeal.id)
        assert (fresh.status.value, fresh.assigned_staff_id, fresh.version) == ("new", None, appeal.version)
        cards = list(s.scalars(select(Outbox).where(Outbox.purpose == "mcp_proposal")))
        events = list(s.scalars(select(AppealEvent.action).where(AppealEvent.appeal_id == appeal.id)))
    assert sorted(c.chat_id for c in cards) == sorted([COORDINATOR_TG, DEPUTY_TG])
    buttons = [b["callback_data"] for row in cards[0].payload["reply_markup"]["inline_keyboard"] for b in row]
    assert buttons == [callback(ASSIGN, appeal.id, appeal.version, world.staff_a.id)]  # adi təyinat axını
    assert "Rəşad Hüseynov" in cards[0].payload["text"] and "pensiya üzrə təcrübəlidir" in cards[0].payload["text"]
    assert events[-1] == "assignment_proposed"


@pytest.mark.parametrize("body, status", [
    ({"number": "MR-2026-999999", "staff_id": 1}, 404),
    ({"number": "{number}", "staff_id": 999999}, 422),  # belə əməkdaş yoxdur
])
def test_invalid_proposals_are_rejected(client, app_engine, world, body, status):
    appeal = make_appeal(app_engine, world)
    body = {**body, "number": body["number"].format(number=appeal.number)}
    assert post(client, "/admin/proposals/assign", body).status_code == status


def test_closed_appeal_cannot_get_a_proposal(client, app_engine, world):
    appeal = make_appeal(app_engine, world)
    with Session(app_engine) as s, s.begin():
        s.execute(text("UPDATE appeals SET status = 'closed' WHERE id = :id"), {"id": appeal.id})
    assert post(client, "/admin/proposals/assign", {"number": appeal.number, "staff_id": world.staff_a.id}
                ).status_code == 409
