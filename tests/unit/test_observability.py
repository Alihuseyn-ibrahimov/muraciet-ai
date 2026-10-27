"""LangSmith: xarici xidmətə yalnız maskalanmış LLM çağırışları gedir, qrafın xam vəziyyəti heç vaxt."""
import os

import pytest
from langchain_core.messages import HumanMessage

from app import observability
from app.agents.schemas import FaqAnswer
from app.privacy.llm import PrivateLLM
from app.privacy.masking import Pseudonymizer
from tests.fakes import FakeChatModel

pytestmark = pytest.mark.unit
SECRET = "Mən Əli Məmmədov, FİN 5ABC12D, telefon 050 123 45 67"


class FakeClient:
    def __init__(self):
        self.runs = []

    def create_run(self, **kwargs):
        self.runs.append(kwargs)

    def update_run(self, run_id, **kwargs):
        self.runs.append(kwargs)


@pytest.fixture
def client(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(observability, "get_client", lambda: fake)
    monkeypatch.setattr(observability.settings, "langsmith_tracing", True)
    monkeypatch.setattr(observability.settings, "langsmith_api_key", "test-key")
    return fake


def run_llm():
    llm = PrivateLLM("faq", model=FakeChatModel(script=[{"answerable": False}]))
    llm.structured([HumanMessage(SECRET)], FaqAnswer, Pseudonymizer({"AD": ["Əli"], "SOYAD": ["Məmmədov"]}))


def test_llm_calls_are_traced_only_in_masked_form(client):
    run_llm()
    sent = str(client.runs)
    assert client.runs and "[FIN_1]" in sent and "[AD_1]" in sent
    assert "5ABC12D" not in sent and "Məmmədov" not in sent and "123 45 67" not in sent


def test_nothing_is_traced_when_disabled(client, monkeypatch):
    monkeypatch.setattr(observability.settings, "langsmith_tracing", False)
    run_llm()
    assert client.runs == []


def test_tracing_needs_an_api_key(client, monkeypatch):
    monkeypatch.setattr(observability.settings, "langsmith_api_key", "")
    run_llm()
    assert client.runs == []


def test_langchain_auto_tracing_is_switched_off(monkeypatch):
    # Avtomatik izləmə LangGraph qrafının xam vəziyyətini (vətəndaşın mətni) də göndərərdi
    for var in observability.AUTO_TRACING_VARS:
        monkeypatch.setenv(var, "true")
    observability.disable_auto_tracing()
    assert all(os.environ[var] == "false" for var in observability.AUTO_TRACING_VARS)


def test_scrub_masks_contact_data_and_names_as_a_second_layer():
    scrubbed = observability.scrub({"messages": [{"content": "Qonşum Hüseynova Nigar, tel 050 123 45 67"}]})
    text = str(scrubbed)
    assert "Hüseynova" not in text and "123 45 67" not in text and "[SEXS_1]" in text
