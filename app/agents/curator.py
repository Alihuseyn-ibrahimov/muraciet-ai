"""9. Kurator agenti (spec 15: "təsdiqlənmiş cavablardan bilik bazası"): vətəndaşın 👍 verdiyi rəsmi cavabdan
bilik bazası üçün ümumiləşdirilmiş sual-cavab təklif edir.

AI təklif edir, insan qərar verir: təklif adminə kart kimi gedir, bilik bazasına yalnız təsdiqdən sonra düşür.
Şəxsi məlumata qarşı deterministik qoruma: təklifin mətni maskalanmadan dəyişməz çıxmalıdır (profil, telefon,
FİN, başqa şəxsin adı yoxdur) və "[...]" kimi yarımçıq yer olmamalıdır. Əks halda təklif atılır.
"""
import logging
from collections.abc import Callable

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.base import Agent
from app.agents.schemas import KbProposal
from app.knowledge.kb import KnowledgeHit, format_hits
from app.privacy.masking import PLACEHOLDER, Pseudonymizer
from app.workflow.service import has_placeholder

log = logging.getLogger(__name__)


class CuratorAgent(Agent[KbProposal]):
    name, schema = "curator", KbProposal

    def propose(self, *, subject: str, summary: str, answer: str, pseudo: Pseudonymizer,
                search: Callable[[str], list[KnowledgeHit]]) -> KbProposal | None:
        try:
            hits = search(pseudo.mask(subject))
        except Exception as e:  # dublikat yoxlaması olmadan təklif vermirik
            log.warning("kurator: axtarış alınmadı (%s)", type(e).__name__)
            return None
        result = self.ask([SystemMessage(self.prompt(sources=format_hits(hits) or "(oxşar məlumat yoxdur)")),
                           HumanMessage(f"Mövzu: {subject}\nXülasə: {summary}\n\nƏməkdaşın rəsmi cavabı:\n{answer}")],
                          pseudo)
        if result is None or not result.suitable:
            return None
        question, text = result.question.strip(), result.answer.strip()
        if not question or not text:
            return None
        for part in (question, text):
            if pseudo.mask(part) != part or PLACEHOLDER.search(part) or has_placeholder(part):
                log.info("kurator: təklifdə şəxsi məlumat və ya yarımçıq yer var, atıldı")
                return None
        return result.model_copy(update={"question": question, "answer": text})
