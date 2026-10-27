import ast
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from pydantic import BaseModel

from app.privacy.llm import PrivateLLM
from app.privacy.masking import Pseudonymizer
from tests.fakes import FakeChatModel

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
PROFILE = {"SOYAD": ["Məmmədov"], "AD": ["Əli"], "UNVAN": ["Nizami küç. 5, m. 12"], "FIN": ["5ABC12D"],
           "TELEFON": ["+994501234567"]}
SECRETS = ["Məmmədov", "Əli", "Nizami küç", "5ABC12D", "+994501234567", "0501234567", "0551112233"]


class Answer(BaseModel):
    text: str


@tool
def search_knowledge_base(query: str) -> str:
    """Bilik bazasında axtarış."""
    return query


def test_structured_call_masks_input_and_restores_output():
    spy = FakeChatModel(script=[{"text": "Hörmətli [AD_1] [SOYAD_1], ünvan [UNVAN_1] üzrə yoxlanılacaq"}])
    llm = PrivateLLM("faq", model=spy)
    answer = llm.structured(
        [SystemMessage("Sən köməkçisən."),
         HumanMessage("Mən Əli Məmmədov, FİN 5ABC12D, tel 0501234567, Nizami küç. 5, m. 12. "
                      "Qonşum 0551112233 nömrəsindən zəng edir.")],
        Answer, Pseudonymizer(PROFILE),
    )
    sent = spy.all_text()
    assert not [s for s in SECRETS if s in sent]  # LLM heç bir şəxsi məlumat görmədi (qonşunun nömrəsi də)
    assert answer.text == "Hörmətli Əli Məmmədov, ünvan Nizami küç. 5, m. 12 üzrə yoxlanılacaq"


def test_structured_output_uses_native_json_schema_not_forced_tool_call():
    # Sonnet 5.5 məcburi tool_choice-u 400 ilə rədd edir; native structured outputs isə sxemə uyğun JSON-u
    # zəmanətləndirir və thinking ilə də işləyir (claude-api skill, Sonnet 5.5 miqrasiyası)
    spy = FakeChatModel(script=[{"text": "ok"}])
    PrivateLLM("faq", model=spy).structured([HumanMessage("sual")], Answer, Pseudonymizer(PROFILE))
    assert spy.structured_methods == ["json_schema"]


def test_tool_calls_are_masked_on_the_way_in_and_restored_on_the_way_out():
    pseudo = Pseudonymizer(PROFILE)
    history = [
        HumanMessage("Əli Məmmədov üçün arayış"),
        AIMessage("", tool_calls=[{"name": "search_knowledge_base", "args": {"query": "Əli arayış"}, "id": "call_ab12cd3"}]),
        ToolMessage("Arayış 2 iş gününə hazırlanır", tool_call_id="call_ab12cd3"),
    ]
    spy = FakeChatModel(script=[AIMessage("", tool_calls=[
        {"name": "search_knowledge_base", "args": {"query": "[AD_1] [SOYAD_1] arayış"}, "id": "call_x"}])])
    reply = PrivateLLM("drafter", model=spy).with_tools(history, [search_knowledge_base], pseudo)
    sent_messages = spy.received[0]
    assert sent_messages[1].tool_calls[0]["args"]["query"] == "[AD_1] arayış"
    assert sent_messages[1].tool_calls[0]["id"] == "call_ab12cd3"  # id-lər toxunulmaz qalır (cütlük qırılmasın)
    assert sent_messages[2].tool_call_id == "call_ab12cd3"
    assert reply.tool_calls[0]["args"]["query"] == "Əli Məmmədov arayış"


def test_content_blocks_are_masked_but_ids_are_not():
    spy = FakeChatModel(script=[{"text": "ok"}])
    blocks = [{"type": "text", "text": "Əli yazır"}, {"type": "tool_use", "id": "toolu_5abc12d", "name": "x",
                                                      "input": {"q": "Əli"}}]
    PrivateLLM("faq", model=spy).structured([AIMessage(blocks)], Answer, Pseudonymizer(PROFILE))
    sent = spy.received[0][0].content
    assert sent[0]["text"] == "[AD_1] yazır" and sent[1]["input"] == {"q": "[AD_1]"}
    assert sent[1]["id"] == "toolu_5abc12d"


# ---------- arxitektura testi: agentlər LLM-ə yalnız PrivateLLM ilə çıxır ----------
FORBIDDEN = ("langchain_anthropic", "langchain_openai", "langchain_ollama", "anthropic", "openai", "app.llm")


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def test_agents_never_import_llm_providers_directly():
    files = list((ROOT / "app" / "agents").rglob("*.py"))
    assert files, "agent faylları tapılmadı"
    violations = {
        f.relative_to(ROOT).as_posix(): sorted(m for m in imported_modules(f)
                                               if any(m == bad or m.startswith(bad + ".") for bad in FORBIDDEN))
        for f in files
    }
    assert not {k: v for k, v in violations.items() if v}, violations
