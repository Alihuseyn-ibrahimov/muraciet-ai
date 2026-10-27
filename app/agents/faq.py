"""3. Məlumat agenti: yalnız bilik bazasına əsaslanan cavab verir (RAG, #14).

Axtarış əvvəlcə sabit kodla edilir, sonra tək bir LLM çağırışı gəlir (findings.md, Faza 5).
Uydurmaya qarşı üç qoruma var:
1. mənbə tapılmasa, LLM ümumiyyətlə çağırılmır;
2. model answerable=true deyib mövcud mənbəyə istinad etmirsə, cavab qəbul olunmur;
3. sorğu maskalanmış mətndir.

Keş: eyni müstəqil sual (dialoqsuz, şəxsi məlumatsız) ikinci dəfə LLM-ə getmir. Açar sualın və tapılan
mənbələrin mətninin heşidir: bilik bazası dəyişəndə köhnə cavab avtomatik işləmir. Keşdə sualın mətni
saxlanmır, yalnız heş və ümumi cavab. Keş əlçatan deyilsə agent onsuz işləyir.
"""
import hashlib
import json
import logging
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Protocol

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.base import Agent, format_dialogue
from app.agents.schemas import FaqAnswer
from app.knowledge.kb import KnowledgeHit, format_hits
from app.privacy.llm import PrivateLLM
from app.privacy.masking import Pseudonymizer, az_lower

log = logging.getLogger(__name__)
NOT_ANSWERABLE = FaqAnswer(answerable=False)


@dataclass(frozen=True)
class FaqResult:
    answer: FaqAnswer
    sources: list[KnowledgeHit] = field(default_factory=list)


class KV(Protocol):
    """Redis-in lazım olan hissəsi."""
    def get(self, key: str) -> bytes | str | None: ...
    def set(self, key: str, value: str, ex: int | None = None) -> object: ...


class MemoryKV:
    """Testlər üçün (müddət nəzərə alınmır)."""

    def __init__(self):
        self.data: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.data.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.data[key] = value


def _normalize(question: str) -> str:
    return " ".join(re.sub(r"[?!.,;:]+", " ", az_lower(question)).split())


class FaqCache:
    def __init__(self, store: KV, *, ttl_seconds: int, prefix: str = "faq:cache"):
        self.store, self.ttl, self.prefix = store, ttl_seconds, prefix

    def _key(self, question: str, hits: list[KnowledgeHit]) -> str:
        material = "\0".join([_normalize(question), *(f"{h.index}|{h.source}|{h.text}" for h in hits)])
        return f"{self.prefix}:{hashlib.sha256(material.encode()).hexdigest()}"

    def get(self, question: str, hits: list[KnowledgeHit]) -> FaqResult | None:
        try:
            raw = self.store.get(self._key(question, hits))
        except Exception as e:  # Redis yoxdur: keşsiz davam
            log.warning("faq: keş oxunmadı (%s)", type(e).__name__)
            return None
        if raw is None:
            return None
        data = json.loads(raw)
        return FaqResult(FaqAnswer.model_validate(data["answer"]), [KnowledgeHit(**h) for h in data["sources"]])

    def put(self, question: str, hits: list[KnowledgeHit], result: FaqResult) -> None:
        value = json.dumps({"answer": result.answer.model_dump(), "sources": [asdict(h) for h in result.sources]},
                           ensure_ascii=False)
        try:
            self.store.set(self._key(question, hits), value, ex=self.ttl)
        except Exception as e:
            log.warning("faq: keşə yazılmadı (%s)", type(e).__name__)


class FaqAgent(Agent[FaqAnswer]):
    name, schema = "faq", FaqAnswer

    def __init__(self, llm: PrivateLLM | None = None, *, cache: FaqCache | None = None):
        super().__init__(llm)
        self.cache = cache

    def answer(self, question: str, pseudo: Pseudonymizer, *, search: Callable[[str], list[KnowledgeHit]],
               dialogue: list[tuple[str, str]] = ()) -> FaqResult | None:
        masked = pseudo.mask(question)
        try:
            hits = search(masked)
        except Exception as e:  # bilik bazası əlçatan deyil
            log.warning("faq: axtarış alınmadı (%s)", type(e).__name__)
            return None
        if not hits:
            return FaqResult(NOT_ANSWERABLE)

        # Yalnız müstəqil, şəxsi məlumatsız sual: cavab başqa vətəndaşa da eyni olmalıdır
        cacheable = self.cache is not None and not dialogue and masked == question
        if cacheable and (cached := self.cache.get(question, hits)) is not None:
            return cached
        result = self._ask(question, pseudo, hits, dialogue)
        if cacheable and result is not None and result.answer.answerable \
                and pseudo.mask(result.answer.answer) == result.answer.answer:
            self.cache.put(question, hits, result)
        return result

    def _ask(self, question: str, pseudo: Pseudonymizer, hits: list[KnowledgeHit],
             dialogue: list[tuple[str, str]]) -> FaqResult | None:

        context = f"Əvvəlki dialoq:\n{format_dialogue(list(dialogue))}\n\n" if dialogue else ""
        result = self.ask([SystemMessage(self.prompt(sources=format_hits(hits))),
                           HumanMessage(f"{context}Vətəndaşın sualı: {question}")], pseudo)
        if result is None:
            return None
        valid = {hit.index for hit in hits}
        cited = [i for i in result.source_ids if i in valid]
        if not (result.answerable and cited and result.answer.strip()):
            return FaqResult(NOT_ANSWERABLE)
        return FaqResult(result.model_copy(update={"source_ids": cited}), [h for h in hits if h.index in cited])
