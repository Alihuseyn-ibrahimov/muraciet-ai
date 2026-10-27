"""Triaj və Yönləndirmə agentlərini LangGraph-da paralel işə salır (spec 5-ci bölmə, #30, #32).

START-dan iki kənar çıxır, ona görə iki node eyni addımda paralel işləyir. Hər node öz açarına
yazır, nəticələr sonda birləşir. Biri xəta versə, digərinin nəticəsi itmir. Maskalama obyekti
(şəxsi məlumatın açarı) vəziyyətə yazılmır, config ilə ötürülür.
"""
import logging
from typing import TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from app.agents.routing import RoutingAgent
from app.agents.schemas import Routing, Triage
from app.agents.triage import TriageAgent
from app.privacy.masking import Pseudonymizer
from app.yaml_config import Department

log = logging.getLogger(__name__)


class TriageState(TypedDict, total=False):
    subject: str
    summary: str
    description: str
    triage: Triage | None
    routing: Routing | None


def build_triage_graph(triage_agent: TriageAgent, routing_agent: RoutingAgent, departments: list[Department]):
    def triage_node(state: TriageState, config: RunnableConfig) -> dict:
        try:
            return {"triage": triage_agent.classify(state["subject"], state["summary"], state["description"],
                                                    config["configurable"]["pseudo"])}
        except Exception as e:  # agent özü xəta atmır, bu əlavə qorumadır
            log.warning("triaj node-u: %s", type(e).__name__)
            return {"triage": None}

    def routing_node(state: TriageState, config: RunnableConfig) -> dict:
        try:
            return {"routing": routing_agent.route(state["subject"], state["summary"], departments,
                                                   config["configurable"]["pseudo"])}
        except Exception as e:
            log.warning("yönləndirmə node-u: %s", type(e).__name__)
            return {"routing": None}

    graph = StateGraph(TriageState)
    graph.add_node("triage", triage_node)
    graph.add_node("routing", routing_node)
    graph.add_edge(START, "triage")
    graph.add_edge(START, "routing")
    graph.add_edge("triage", END)
    graph.add_edge("routing", END)
    return graph.compile()


def run_triage(graph, *, subject: str, summary: str, description: str,
               pseudo: Pseudonymizer) -> tuple[Triage | None, Routing | None]:
    result = graph.invoke({"subject": subject, "summary": summary, "description": description},
                          config={"configurable": {"pseudo": pseudo}})
    return result.get("triage"), result.get("routing")
