"""LLM-ə yeganə yol (spec 5-ci və 9-cu bölmələr).

Bütün agentlər LLM-ə yalnız bu sinif vasitəsilə müraciət edir. Mesajlar göndərilməzdən əvvəl
maskalanır, cavab və tool arqumentləri qayıdanda bərpa olunur. Beləliklə, maskalama 8 agentin
hər birində ayrıca yox, bir yerdə təmin olunur. Agentlərin provayder kitabxanalarını birbaşa
import etmədiyini tests/unit/test_private_llm.py yoxlayır.

Tool çağırışlarının id-ləri toxunulmaz qalır: maskalansalar ("call_ab12cd3" FİN kimi görünə bilər),
tool_use ilə tool_result cütlüyü qırılar.
"""
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from pydantic import BaseModel

from app.config import settings
from app.observability import traced
from app.privacy.masking import Pseudonymizer

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)
GUARDRAILS_CONFIG = Path(__file__).resolve().parents[1] / "guardrails" / "config"


def _map_content(content: Any, text_fn: Callable[[str], str], obj_fn: Callable[[Any], Any]) -> Any:
    if isinstance(content, str):
        return text_fn(content)
    blocks = []
    for block in content:
        if isinstance(block, str):
            blocks.append(text_fn(block))
        elif isinstance(block, dict):
            copy = dict(block)
            if isinstance(copy.get("text"), str):
                copy["text"] = text_fn(copy["text"])
            if "input" in copy:  # tool_use blokunun arqumentləri
                copy["input"] = obj_fn(copy["input"])
            blocks.append(copy)
        else:
            blocks.append(block)
    return blocks


class PrivateLLM:
    def __init__(self, agent: str, model: BaseChatModel | None = None):
        self.agent = agent
        self._model = model

    @property
    def model(self) -> BaseChatModel:
        if self._model is None:
            from app.llm import get_chat_model  # yeganə icazəli yer

            self._model = get_chat_model(self.agent)
        return self._model

    @staticmethod
    def _mask(messages: list[BaseMessage], pseudo: Pseudonymizer) -> list[BaseMessage]:
        masked = []
        for message in messages:
            update: dict[str, Any] = {"content": _map_content(message.content, pseudo.mask, pseudo.mask_obj)}
            if isinstance(message, AIMessage) and message.tool_calls:
                update["tool_calls"] = [{**call, "args": pseudo.mask_obj(call["args"])} for call in message.tool_calls]
            masked.append(message.model_copy(update=update))
        return masked

    def structured(self, messages: list[BaseMessage], schema: type[T], pseudo: Pseudonymizer) -> T:
        # Native structured outputs: məcburi tool_choice-dan (Sonnet 5.5-də 400) asılı deyil və thinking ilə işləyir
        with traced(self.agent):  # izə yalnız maskalanmış mesajlar və bərpadan əvvəlki cavab düşür
            result = self.model.with_structured_output(schema, method="json_schema").invoke(self._mask(messages, pseudo))
        if not isinstance(result, schema):
            result = schema.model_validate(result)
        return pseudo.unmask_obj(result)

    def with_tools(self, messages: list[BaseMessage], tools: list, pseudo: Pseudonymizer) -> AIMessage:
        with traced(self.agent):
            reply = self.model.bind_tools(tools).invoke(self._mask(messages, pseudo))
        return reply.model_copy(update={
            "content": _map_content(reply.content, pseudo.unmask, pseudo.unmask_obj),
            "tool_calls": [{**call, "args": pseudo.unmask_obj(call["args"])} for call in reply.tool_calls],
        })

    def guardrails_allows(self, text: str, pseudo: Pseudonymizer) -> bool | None:
        """NeMo Guardrails yoxlaması (USE_NEMO_GUARDRAILS=true) maskalanmış mətnlə.
        None: NeMo söndürülüb, quraşdırılmayıb və ya xəta verib. Bu halda qayda əsaslı yoxlama işləyir."""
        if not settings.use_nemo_guardrails:
            return None
        try:
            rails = _nemo_rails(self.agent, self.model)
            masked = pseudo.mask(text)
            result = rails.generate(messages=[{"role": "user", "content": masked}], options={"rails": ["input"]})
            content = result.response[0]["content"] if result.response else ""
            return content.strip() == masked.strip()  # yalnız input relsləri işləyəndə bloklanmayan mesaj geri qayıdır
        except Exception as e:
            log.warning("NeMo Guardrails işləmədi (%s): qayda əsaslı yoxlama ilə davam edilir", type(e).__name__)
            return None


_RAILS: dict[str, Any] = {}


def _nemo_rails(agent: str, model: BaseChatModel):
    # Model pydantic obyektidir və hash olunmur, ona görə keş agentin adına görədir
    if agent not in _RAILS:
        from nemoguardrails import LLMRails, RailsConfig

        _RAILS[agent] = LLMRails(RailsConfig.from_path(str(GUARDRAILS_CONFIG)), llm=model)
    return _RAILS[agent]
