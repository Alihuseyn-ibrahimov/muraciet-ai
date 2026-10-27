"""Agent tool-ları və ReAct dövrəsi (#25–26).

search_knowledge_base xətanı istisna kimi atmır, "XƏTA: ..." mətni qaytarır: model bunu oxuyub
başqa yol seçə bilir. Sorğu bilik bazasına göndərilməzdən əvvəl maskalanır.

gather_evidence: model ən çoxu max_calls dəfə axtarış edir. Yekun strukturlaşdırılmış cavab tool
bloklarını deyil, toplanmış dəlilin mətnini alır. Məcburi sxem tool-u ilə köhnə tool bloklarının
eyni sorğuda qarışmasından qaçmaq üçün belə edilir (findings.md, Faza 5).
"""
import logging
from collections.abc import Callable

from langchain_core.messages import BaseMessage, ToolMessage
from langchain_core.tools import BaseTool, tool

from app.knowledge.kb import KnowledgeHit, format_hits
from app.privacy.llm import PrivateLLM
from app.privacy.masking import Pseudonymizer

log = logging.getLogger(__name__)
Search = Callable[[str], list[KnowledgeHit]]
NOTHING_FOUND = "Bilik bazasında uyğun məlumat tapılmadı."


def kb_tool(search: Search, pseudo: Pseudonymizer) -> BaseTool:
    @tool("search_knowledge_base")
    def search_knowledge_base(query: str) -> str:
        """Qurumun rəsmi bilik bazasında (qaydalar, xidmətlər, prosedurlar, müddətlər) axtarış edir."""
        try:
            hits = search(pseudo.mask(query))
        except Exception as e:
            return f"XƏTA: bilik bazası hazırda əlçatan deyil ({type(e).__name__})"
        return format_hits(hits) if hits else NOTHING_FOUND

    return search_knowledge_base


def gather_evidence(llm: PrivateLLM, messages: list[BaseMessage], search_tool: BaseTool, pseudo: Pseudonymizer,
                    *, max_calls: int) -> str:
    history, evidence, calls = list(messages), [], 0
    while calls < max_calls:
        try:
            reply = llm.with_tools(history, [search_tool], pseudo)
        except Exception as e:
            log.warning("ReAct: alətli çağırış alınmadı (%s)", type(e).__name__)
            break
        if not reply.tool_calls:
            break
        history.append(reply)
        for call in reply.tool_calls:
            if calls >= max_calls:
                break
            calls += 1
            result = search_tool.invoke(call["args"])
            evidence.append(result)
            history.append(ToolMessage(content=result, tool_call_id=call["id"]))
    return "\n\n".join(evidence) or NOTHING_FOUND
