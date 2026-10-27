import threading

import pytest

from app.agents.routing import RoutingAgent
from app.agents.staff_pipeline import build_triage_graph, run_triage
from app.agents.triage import TriageAgent
from app.privacy.llm import PrivateLLM
from app.privacy.masking import Pseudonymizer
from app.workflow.states import Category
from app.yaml_config import load_config
from tests.fakes import FakeChatModel

pytestmark = pytest.mark.unit
DEPARTMENTS = load_config().departments
TRIAGE_OK = {"category": "complaint", "priority": "normal", "legal_issue": False, "reason": "narazılıq"}
ROUTING_OK = {"department_code": "pensiya", "reason": "pensiya ödənişi"}


def agents(triage_script, routing_script):
    return (TriageAgent(PrivateLLM("triage", model=FakeChatModel(script=triage_script))),
            RoutingAgent(PrivateLLM("routing", model=FakeChatModel(script=routing_script))))


def run(graph):
    return run_triage(graph, subject="İşıq", summary="Yanmır", description="3 gündür yanmır", pseudo=Pseudonymizer())


def test_both_agents_return_results():
    triage, routing = run(build_triage_graph(*agents([TRIAGE_OK], [ROUTING_OK]), DEPARTMENTS))
    assert triage.category == Category.complaint and routing.department_code == "pensiya"


def test_one_failure_does_not_lose_the_other_result():
    triage, routing = run(build_triage_graph(*agents([RuntimeError("API")], [ROUTING_OK]), DEPARTMENTS))
    assert triage is None and routing.department_code == "pensiya"


def test_agents_run_in_parallel():
    # Ardıcıl işləsəydilər, birinci node ikincini gözləyərkən barrier vaxtı keçərdi
    barrier = threading.Barrier(2, timeout=5)

    class Waiting(FakeChatModel):
        def with_structured_output(self, schema, **kwargs):
            runnable = super().with_structured_output(schema, **kwargs)

            class Wrapped:
                def invoke(self_inner, messages):
                    barrier.wait()
                    return runnable.invoke(messages)

            return Wrapped()

    graph = build_triage_graph(TriageAgent(PrivateLLM("triage", model=Waiting(script=[TRIAGE_OK]))),
                               RoutingAgent(PrivateLLM("routing", model=Waiting(script=[ROUTING_OK]))), DEPARTMENTS)
    triage, routing = run(graph)
    assert triage is not None and routing is not None
