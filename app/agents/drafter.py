"""7. Cavab layihəsi agenti (ReAct, #26): əməkdaş üçün rəsmi cavab layihəsi.

Model nəyi axtaracağını özü seçir (ən çoxu MAX_SEARCHES dəfə). Bilmədiyi faktı uydurmur,
"[...]" ilə boş saxlayır və missing_info-da qeyd edir. None qaytararsa, əməkdaş layihəsiz yazır.
"""
import re
from collections.abc import Callable

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.base import Agent
from app.agents.schemas import Draft
from app.agents.tooling import gather_evidence, kb_tool
from app.knowledge.kb import KnowledgeHit
from app.privacy.masking import Pseudonymizer

MAX_SEARCHES = 3
CLOSING = re.compile(r"\s*(?:Hörmətlə|Ehtiramla)\s*,?[^\n]*\s*$", re.IGNORECASE)


class DrafterAgent(Agent[Draft]):
    name, schema = "drafter", Draft

    def draft(self, appeal_context: str, pseudo: Pseudonymizer, *,
              search: Callable[[str], list[KnowledgeHit]]) -> Draft | None:
        messages = [SystemMessage(self.prompt()), HumanMessage(f"Müraciət:\n{appeal_context}")]
        evidence = gather_evidence(self.llm, messages, kb_tool(search, pseudo), pseudo, max_calls=MAX_SEARCHES)
        result = self.ask([*messages, HumanMessage(
            f"Bilik bazasından toplanmış məlumat:\n\n{evidence}\n\nİndi cavab layihəsini ver.")], pseudo)
        if result is None:
            return None
        # İmzanı sistem əlavə edir (templates.yaml: answer_signature). Model promptdakı qadağaya baxmayaraq
        # bəzən "Hörmətlə," yazır, real yoxlamada imza təkrarlanmışdı
        return result.model_copy(update={"text": CLOSING.sub("", result.text).strip()})
