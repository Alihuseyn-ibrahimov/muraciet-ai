"""Vətəndaş dialoqunun supervisor qrafı (spec 6-cı bölmə, #30, #32).

    START → guard ─(bloklandı)──────────────────────────────────────→ remember → END
                  ├─(addım "intake")→ intake ───────────────────────→ remember
                  └→ router → faq | intake | status | clarification | human | greeting | off_topic → remember

Qraf heç bir müraciəti dəyişmir. O, cavab mətnini, düymə kodlarını və effektləri (məs. start_form)
qaytarır, onları kontroller (Faza 6) icra edir: "agent təklif edir, kod qərar verir".

Yaddaş (#27): son dialoq (history) və qəbul dialoqu checkpoint-də saxlanılır. Maskalama obyekti
(vətəndaşın şifrəsi açılmış profili) isə yalnız config ilə ötürülür, checkpoint-ə yazılmır.
Hər dövrənin başında çıxışlar sıfırlanır, əks halda əvvəlki dövrənin effekti təkrar icra olunardı.
"""
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Annotated, Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from app.agents.faq import FaqAgent
from app.agents.guard import GuardAgent
from app.agents.intake import IntakeAgent
from app.agents.router import RouterAgent, RouterContext
from app.config import settings
from app.knowledge.kb import KnowledgeHit
from app.privacy.masking import Pseudonymizer
from app.yaml_config import Templates

HISTORY_LIMIT = 20
INTAKE_STEPS = ("intake", "intake_new")
BTN_FAQ_HELPFUL, BTN_START_APPEAL, BTN_DISMISS = "faq_helpful", "start_appeal", "dismiss"
INTENT_NODES = {
    "question": "faq", "new_appeal": "intake", "status": "status", "clarification_reply": "clarification",
    "human": "human", "greeting": "greeting", "off_topic": "off_topic",
}


def _remember(old: list | None, new: list | None) -> list:
    return (list(old or []) + list(new or []))[-HISTORY_LIMIT:]


class CitizenState(TypedDict, total=False):
    # giriş
    text: str
    step: str  # "idle" | "intake" | "intake_new" (düymə ilə yeni qəbul, marşrutlaşdırıcısız)
    has_open_question: bool
    # yaddaş (checkpoint-də saxlanılır)
    history: Annotated[list, _remember]
    intake_dialogue: list
    # çıxış (hər dövrədə sıfırlanır)
    intent: str | None
    reply: str | None
    buttons: list[str]
    effects: list[dict]
    next_step: str
    resolved_by_ai: bool
    sources: list[str]


@dataclass
class CitizenAgents:
    guard: GuardAgent
    router: RouterAgent
    faq: FaqAgent
    intake: IntakeAgent

    @classmethod
    def default(cls) -> "CitizenAgents":
        return cls(GuardAgent(), RouterAgent(), FaqAgent(), IntakeAgent())


@dataclass(frozen=True)
class CitizenTurn:
    intent: str
    reply: str | None
    next_step: str
    buttons: list[str] = field(default_factory=list)
    effects: list[dict] = field(default_factory=list)
    resolved_by_ai: bool = False
    sources: list[str] = field(default_factory=list)


def _pseudo(config: RunnableConfig) -> Pseudonymizer:
    return config["configurable"]["pseudo"]


def build_citizen_graph(agents: CitizenAgents, *, search: Callable[[str], list[KnowledgeHit]], templates: Templates,
                        org_name: str, hotline: str, checkpointer: BaseCheckpointSaver):
    def guard(state: CitizenState, config: RunnableConfig) -> dict:
        reset = {"intent": None, "reply": None, "buttons": [], "effects": [], "resolved_by_ai": False,
                 "sources": [], "next_step": "idle"}
        if agents.guard.check(state["text"], _pseudo(config)).allowed:
            return reset
        keep = "intake" if state.get("step") in INTAKE_STEPS else "idle"  # qəbul dialoqu bloklanan mesajla qırılmır
        return reset | {"intent": "blocked", "reply": templates.render("blocked", org_name=org_name), "next_step": keep}

    def after_guard(state: CitizenState) -> str:
        if state.get("intent") == "blocked":
            return "remember"
        return "intake" if state.get("step") in INTAKE_STEPS else "router"

    def router(state: CitizenState, config: RunnableConfig) -> dict:
        found = agents.router.classify(state["text"], dialogue=list(state.get("history") or [])[-6:],
                                       context=RouterContext(bool(state.get("has_open_question"))),
                                       pseudo=_pseudo(config))
        return {"intent": found.intent if found else "question"}  # təhlükəsiz standart

    def faq(state: CitizenState, config: RunnableConfig) -> dict:
        result = agents.faq.answer(state["text"], _pseudo(config), search=search,
                                   dialogue=list(state.get("history") or [])[-4:])
        if result is None:
            return {"reply": templates.render("llm_unavailable"), "buttons": [BTN_START_APPEAL]}
        if not result.answer.answerable:
            return {"reply": templates.render("no_answer_offer_appeal"), "buttons": [BTN_START_APPEAL, BTN_DISMISS]}
        titles = list(dict.fromkeys(hit.title for hit in result.sources))
        return {"reply": f"{result.answer.answer}\n\nMənbə: {'; '.join(titles)}",
                "buttons": [BTN_FAQ_HELPFUL, BTN_START_APPEAL], "resolved_by_ai": True, "sources": titles}

    def intake(state: CitizenState, config: RunnableConfig) -> dict:
        previous = list(state.get("intake_dialogue") or []) if state.get("step") == "intake" else []
        dialogue = [*[(role, text) for role, text in previous], ("citizen", state["text"])]
        asked = sum(1 for role, _ in dialogue if role == "bot")
        result = agents.intake.step(dialogue, questions_asked=asked, pseudo=_pseudo(config))
        if not result.complete:
            return {"intent": "new_appeal", "reply": result.next_question, "next_step": "intake",
                    "intake_dialogue": [*dialogue, ("bot", result.next_question)]}
        description = "\n".join(text for role, text in dialogue if role == "citizen")
        return {"intent": "new_appeal", "next_step": "form", "intake_dialogue": [], "effects": [
            {"type": "start_form", "subject": result.subject, "summary": result.summary, "description": description}]}

    def status(state: CitizenState) -> dict:
        return {"effects": [{"type": "show_status"}]}

    def clarification(state: CitizenState) -> dict:
        return {"effects": [{"type": "forward_clarification", "text": state["text"]}]}

    def human(state: CitizenState) -> dict:
        return {"reply": templates.render("human_handoff", hotline=f"\n{hotline}" if hotline else ""),
                "buttons": [BTN_START_APPEAL]}

    def greeting(state: CitizenState) -> dict:
        return {"reply": templates.render("greeting", org_name=org_name)}

    def off_topic(state: CitizenState) -> dict:
        return {"reply": templates.render("off_topic", org_name=org_name)}

    def remember(state: CitizenState) -> dict:
        entries: list[Any] = [("citizen", state["text"])]
        if state.get("reply"):
            entries.append(("bot", state["reply"]))
        return {"history": entries}

    graph = StateGraph(CitizenState)
    nodes = {"guard": guard, "router": router, "faq": faq, "intake": intake, "status": status,
             "clarification": clarification, "human": human, "greeting": greeting, "off_topic": off_topic,
             "remember": remember}
    for name, fn in nodes.items():
        graph.add_node(name, fn)
    graph.add_edge(START, "guard")
    graph.add_conditional_edges("guard", after_guard, ["remember", "intake", "router"])
    graph.add_conditional_edges("router", lambda s: INTENT_NODES[s["intent"]], list(set(INTENT_NODES.values())))
    for name in set(INTENT_NODES.values()):
        graph.add_edge(name, "remember")
    graph.add_edge("remember", END)
    return graph.compile(checkpointer=checkpointer)


def run_turn(graph, *, citizen_key: int | str, text: str, step: str, has_open_question: bool,
             pseudo: Pseudonymizer) -> CitizenTurn:
    out = graph.invoke(
        {"text": text, "step": step, "has_open_question": has_open_question},
        config={"configurable": {"thread_id": f"citizen:{citizen_key}", "pseudo": pseudo}},
    )
    return CitizenTurn(intent=out.get("intent") or "question", reply=out.get("reply"),
                       next_step=out.get("next_step") or "idle", buttons=list(out.get("buttons") or []),
                       effects=list(out.get("effects") or []), resolved_by_ai=bool(out.get("resolved_by_ai")),
                       sources=list(out.get("sources") or []))


@lru_cache
def postgres_checkpointer() -> BaseCheckpointSaver:
    """İstehsal checkpointer-i (tətbiq rolu ilə). Cədvəllər `manage.py migrate` zamanı yaradılır."""
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg_pool import ConnectionPool

    settings.require("app_database_url")
    pool = ConnectionPool(conninfo=settings.app_database_url.replace("postgresql+psycopg://", "postgresql://"),
                          max_size=5, kwargs={"autocommit": True, "prepare_threshold": 0}, open=True)
    return PostgresSaver(pool)
