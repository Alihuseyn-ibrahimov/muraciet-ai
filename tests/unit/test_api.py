"""Yeni API: botların update endpoint-i (servis tokeni) və veb-vidcetin sual endpoint-i."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.agents.citizen_graph import CitizenTurn
from app.api import create_app
from app.api.deps import get_citizen_controller, get_staff_controller, get_web_turn
from app.yaml_config import load_config

pytestmark = pytest.mark.unit
TOKEN = "test-service-token-0001"
T = load_config().templates.texts


class FakeController:
    def __init__(self, error: Exception | None = None):
        self.updates, self.error = [], error

    def handle(self, update: dict) -> None:
        if self.error:
            raise self.error
        self.updates.append(update)


@pytest.fixture
def make_client(monkeypatch):
    monkeypatch.setattr("app.api.deps.settings.service_token", TOKEN)
    monkeypatch.setattr("app.api.deps.settings.citizen_bot_username", "test_muraciet_bot")

    def build(controller=None, turn=None, staff=None) -> TestClient:
        from app.api.deps import get_web_limiter
        from app.citizen.session import MemoryLimiter
        app = create_app()
        limiter = MemoryLimiter()
        app.dependency_overrides[get_web_limiter] = lambda: limiter
        if controller is not None:
            app.dependency_overrides[get_citizen_controller] = lambda: controller
        if staff is not None:
            app.dependency_overrides[get_staff_controller] = lambda: staff
        if turn is not None:
            app.dependency_overrides[get_web_turn] = lambda: turn
        return TestClient(app, raise_server_exceptions=False)
    return build


def test_update_requires_the_service_token(make_client):
    controller = FakeController()
    client = make_client(controller)
    assert client.post("/bot/citizen/update", json={"update_id": 1}).status_code == 401
    assert client.post("/bot/citizen/update", json={"update_id": 1},
                       headers={"X-Service-Token": "wrong"}).status_code == 401
    assert controller.updates == []
    ok = client.post("/bot/citizen/update", json={"update_id": 1}, headers={"X-Service-Token": TOKEN})
    assert ok.status_code == 200 and controller.updates == [{"update_id": 1}]


def test_empty_service_token_never_authenticates(make_client, monkeypatch):
    monkeypatch.setattr("app.api.deps.settings.service_token", "")
    client = make_client(FakeController())
    assert client.post("/bot/citizen/update", json={}, headers={"X-Service-Token": ""}).status_code == 401


def test_database_outage_asks_the_runner_to_retry(make_client):
    client = make_client(FakeController(OperationalError("SELECT 1", {}, Exception("down"))))
    response = client.post("/bot/citizen/update", json={"update_id": 2}, headers={"X-Service-Token": TOKEN})
    assert response.status_code == 503


def test_a_broken_update_is_dropped_so_the_bot_does_not_get_stuck(make_client):
    client = make_client(FakeController(ValueError("gözlənilməz forma")))
    response = client.post("/bot/citizen/update", json={"update_id": 3}, headers={"X-Service-Token": TOKEN})
    assert response.status_code == 200 and response.json() == {"ok": False}


def turn_returning(**kwargs):
    calls = []

    def turn(**call):
        calls.append(call)
        return CitizenTurn(**({"intent": "question", "reply": "Cavab", "next_step": "idle"} | kwargs))
    turn.calls = calls
    return turn


def test_web_chat_answers_questions(make_client):
    turn = turn_returning(reply="Pensiya ayın 8-15-də köçürülür.")
    response = make_client(turn=turn).post("/api/chat", json={"session_id": "abc", "message": "Pensiya nə vaxt?"})
    assert response.json() == {"reply": "Pensiya ayın 8-15-də köçürülür.", "intent": "question"}
    assert turn.calls[0]["citizen_key"] == "web:abc" and turn.calls[0]["step"] == "idle"


@pytest.mark.parametrize("intent", ["new_appeal", "status", "clarification_reply"])
def test_web_chat_sends_appeals_to_telegram(make_client, intent):
    turn = turn_returning(intent=intent, reply="Hansı bankdır?", effects=[{"type": "start_form"}])
    reply = make_client(turn=turn).post("/api/chat", json={"session_id": "abc", "message": "x"}).json()["reply"]
    assert reply == T["web_use_telegram"].format(bot="@test_muraciet_bot")


def test_web_chat_validates_input(make_client):
    client = make_client(turn=turn_returning())
    assert client.post("/api/chat", json={"session_id": "", "message": "x"}).status_code == 422
    assert client.post("/api/chat", json={"session_id": "a", "message": "x" * 4001}).status_code == 422


def test_staff_updates_go_to_the_staff_controller(make_client):
    citizen, staff = FakeController(), FakeController()
    client = make_client(citizen, staff=staff)
    assert client.post("/bot/staff/update", json={"update_id": 9}).status_code == 401
    ok = client.post("/bot/staff/update", json={"update_id": 9}, headers={"X-Service-Token": TOKEN})
    assert ok.status_code == 200 and staff.updates == [{"update_id": 9}] and citizen.updates == []


# ---------- n8n üçün daxili endpoint-lər ----------
class FakeInternal:
    def __init__(self):
        self.calls = []

    def sla_check(self):
        self.calls.append(("sla",))
        return {"notified": 2, "auto_closed": 1}

    def notify(self, target, text, only_workday=False):
        self.calls.append(("notify", target, text, only_workday))
        return 3

    def retention(self):
        self.calls.append(("retention",))
        return {"skipped": True}

    def weekly_report(self, days=7):
        self.calls.append(("report", days))
        return {"sent": 1, "ai": True}


@pytest.fixture
def internal(make_client):
    from app.api.deps import get_internal
    fake = FakeInternal()
    client = make_client()
    client.app.dependency_overrides[get_internal] = lambda: fake
    return client, fake


def test_internal_endpoints_require_the_service_token(internal):
    client, fake = internal
    for path in ("/internal/sla/check", "/internal/retention/cleanup"):
        assert client.post(path).status_code == 401
    assert client.post("/internal/notify", json={"target": "head", "text": "x"}).status_code == 401
    assert fake.calls == []


def test_internal_endpoints_call_the_backend(internal):
    client, fake = internal
    headers = {"X-Service-Token": TOKEN}
    assert client.post("/internal/sla/check", headers=headers).json() == {"notified": 2, "auto_closed": 1}
    assert client.post("/internal/notify", headers=headers,
                       json={"target": "head", "text": "📊 Hesabat"}).json() == {"sent": 3}
    assert client.post("/internal/retention/cleanup", headers=headers).json() == {"skipped": True}
    assert client.post("/internal/notify", headers=headers,
                       json={"target": "head", "text": "📊", "only_workday": True}).json() == {"sent": 3}
    assert fake.calls == [("sla",), ("notify", "head", "📊 Hesabat", False), ("retention",),
                          ("notify", "head", "📊", True)]


def test_notify_validates_target_and_length(internal):
    client, _ = internal
    headers = {"X-Service-Token": TOKEN}
    assert client.post("/internal/notify", headers=headers, json={"target": "all", "text": "x"}).status_code == 422
    assert client.post("/internal/notify", headers=headers,
                       json={"target": "head", "text": "x" * 4001}).status_code == 422


def test_web_widget_page_is_served_and_uses_the_new_chat_api(make_client):
    page = make_client().get("/")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    assert "session_id" in page.text and "/api/reset" not in page.text


def test_web_chat_is_rate_limited_per_client(make_client, monkeypatch):
    monkeypatch.setattr("app.api.web.settings.rate_limit_per_minute", 2)
    turn = turn_returning()
    client = make_client(turn=turn)
    for _ in range(2):
        assert client.post("/api/chat", json={"session_id": "a", "message": "x"}).status_code == 200
    blocked = client.post("/api/chat", json={"session_id": "b", "message": "x"})  # yeni sessiya limitdən qaçmır
    assert blocked.status_code == 429 and blocked.json()["reply"] == T["rate_limited"]
    assert len(turn.calls) == 2  # limitdən sonra LLM çağırılmır


def test_weekly_report_endpoint(internal):
    client, fake = internal
    assert client.post("/internal/report/weekly").status_code == 401
    headers = {"X-Service-Token": TOKEN}
    assert client.post("/internal/report/weekly", headers=headers).json() == {"sent": 1, "ai": True}
    assert client.post("/internal/report/weekly", headers=headers, json={"days": 14}).status_code == 200
    assert client.post("/internal/report/weekly", headers=headers, json={"days": 90}).status_code == 422
    assert fake.calls == [("report", 7), ("report", 14)]
