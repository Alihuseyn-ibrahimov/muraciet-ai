"""Agent modellərinin qurulması: Claude 5.5 modellərində thinking defolt olaraq açıqdır və max_tokens-ə daxildir."""
import pytest

from app.llm import build_chat_model, get_chat_model

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    monkeypatch.setattr("app.llm.settings.anthropic_api_key", "test-key")
    get_chat_model.cache_clear()
    yield
    get_chat_model.cache_clear()


def test_anthropic_model_gets_effort_and_room_for_thinking():
    model = build_chat_model("anthropic", "claude-haiku-5-5", effort="low")
    assert model.reasoning_effort == "low"
    assert model.max_tokens >= 8000  # 2048 olanda thinking cavabı kəsə bilər (stop_reason=max_tokens)
    assert build_chat_model("anthropic", "claude-sonnet-5-5").reasoning_effort is None  # modelin öz standartı


def test_agent_model_takes_effort_from_agents_yaml():
    assert get_chat_model("router").reasoning_effort == "low"
