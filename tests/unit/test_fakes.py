import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from pydantic import BaseModel, ValidationError

from tests.fakes import FakeChatModel

pytestmark = pytest.mark.unit


class Foo(BaseModel):
    n: int


@tool
def search(q: str) -> str:
    """Test tool-u."""
    return q


def test_text_responses_in_order():
    model = FakeChatModel(script=["birinci", "ikinci"])
    assert model.invoke("x").content == "birinci"
    assert model.invoke("y").content == "ikinci"


def test_records_received_messages():
    model = FakeChatModel(script=["ok"])
    model.invoke([SystemMessage("sistem"), HumanMessage("sual")])
    assert [m.content for m in model.received[0]] == ["sistem", "sual"]


def test_structured_output_from_dict_and_instance():
    model = FakeChatModel(script=[{"n": 1}, Foo(n=2)])
    structured = model.with_structured_output(Foo)
    assert structured.invoke([HumanMessage("a")]) == Foo(n=1)
    assert structured.invoke([HumanMessage("b")]) == Foo(n=2)
    assert len(model.received) == 2


def test_structured_output_invalid_dict_raises_validation_error():
    model = FakeChatModel(script=[{"n": "rəqəm deyil"}])
    with pytest.raises(ValidationError):
        model.with_structured_output(Foo).invoke([HumanMessage("a")])


def test_bind_tools_returns_scripted_tool_call():
    call = AIMessage("", tool_calls=[{"name": "search", "args": {"q": "x"}, "id": "c1"}])
    model = FakeChatModel(script=[call])
    result = model.bind_tools([search]).invoke("sual")
    assert result.tool_calls[0]["name"] == "search"
    assert result.tool_calls[0]["args"] == {"q": "x"}


def test_script_exception_is_raised():
    model = FakeChatModel(script=[RuntimeError("provayder düşüb")])
    with pytest.raises(RuntimeError, match="provayder düşüb"):
        model.invoke("x")


def test_exhausted_script_raises_assertion():
    model = FakeChatModel(script=[])
    with pytest.raises(AssertionError, match="ssenari bitdi"):
        model.invoke("x")


def test_all_text_joins_everything_sent_to_model():
    model = FakeChatModel(script=["a", {"n": 1}])
    model.invoke([SystemMessage("sistem mətni"), HumanMessage("FİN 5ABC12D")])
    model.with_structured_output(Foo).invoke([HumanMessage("telefon +994501234567")])
    text = model.all_text()
    assert "sistem mətni" in text and "5ABC12D" in text and "+994501234567" in text
