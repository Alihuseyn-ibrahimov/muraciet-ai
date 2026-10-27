"""Agentlərin ortaq bazası (spec 5-ci bölmə: "bir agent, bir iş").

Hər agentin öz promptu (prompts/<ad>.md), öz çıxış sxemi və LLM-ə yalnız PrivateLLM ilə çıxışı var.
Cavab sxemə uymasa, agent xəta mətni ilə bir dəfə təkrar cəhd edir. Yenə alınmasa, provayder xəta
versə də, None qaytarır və çağıran tərəf təhlükəsiz standarta keçir. Agent heç vaxt istisna atmır,
çünki AI-nin xətası vətəndaşın və ya əməkdaşın işini dayandırmamalıdır.
"""
import logging
from pathlib import Path
from typing import ClassVar, Generic, TypeVar

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import BaseMessage, HumanMessage
from pydantic import BaseModel, ValidationError

from app.privacy.llm import PrivateLLM
from app.privacy.masking import Pseudonymizer

log = logging.getLogger(__name__)
PROMPTS = Path(__file__).resolve().parent / "prompts"
ROLE_LABELS = {"citizen": "Vətəndaş", "bot": "Bot", "staff": "Əməkdaş"}
T = TypeVar("T", bound=BaseModel)


def format_dialogue(dialogue: list[tuple[str, str]]) -> str:
    return "\n".join(f"{ROLE_LABELS.get(role, role)}: {text}" for role, text in dialogue) or "(dialoq yoxdur)"


class Agent(Generic[T]):
    name: ClassVar[str]
    schema: ClassVar[type[BaseModel]]

    def __init__(self, llm: PrivateLLM | None = None):
        self.llm = llm or PrivateLLM(self.name)

    def prompt(self, **values: object) -> str:
        text = (PROMPTS / f"{self.name}.md").read_text(encoding="utf-8")
        for key, value in values.items():  # str.format deyil: promptdakı adi fiqurlu mötərizələr sınmasın
            text = text.replace("{" + key + "}", str(value))
        return text

    def ask(self, messages: list[BaseMessage], pseudo: Pseudonymizer) -> T | None:
        try:
            return self.llm.structured(messages, self.schema, pseudo)
        except (ValidationError, OutputParserException) as e:
            retry = [*messages, HumanMessage(
                f"Cavabın sxemə uyğun deyil: {str(e)[:500]}. Yalnız tələb olunan formatda yenidən cavab ver.")]
            try:
                return self.llm.structured(retry, self.schema, pseudo)
            except Exception as again:
                log.warning("agent %s: təkrar cəhd də alınmadı (%s)", self.name, type(again).__name__)
                return None
        except Exception as e:  # provayder xətası, şəbəkə, limit
            log.warning("agent %s: LLM çağırışı alınmadı (%s)", self.name, type(e).__name__)
            return None
