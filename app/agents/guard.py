"""1. Təhlükəsizlik agenti: prompt injection, təhqir və həddən uzun mesajları bloklayır.

Əvvəlcə qaydalar işləyir: sürətlidir, pulsuzdur, LLM-ə heç nə göndərmir. Qaydalar keçəndə,
USE_NEMO_GUARDRAILS=true olduqda NeMo Guardrails maskalanmış mətnlə yoxlayır. NeMo işləməsə, qayda
əsaslı nəticə qüvvədə qalır.
"""
import re

from app.agents.schemas import GuardResult
from app.privacy.llm import PrivateLLM
from app.privacy.masking import Pseudonymizer, az_lower

MAX_INPUT_CHARS = 4000
_INJECTION = [re.compile(p) for p in (
    r"ignore\s+(all\s+|the\s+)?(previous|above|prior)\s+instructions",
    r"\b(system|sistem)\s+prompt",
    r"\byou\s+are\s+now\b",
    r"\bdeveloper\s+mode\b",
    r"\bjailbreak\b",
    r"(əvvəlki|evvelki)\s+(bütün\s+|butun\s+)?(təlimat|telimat|göstəriş|gosteris)\w*\s+"
    r"(unut|nəzərə\s+alma|nezere\s+alma|ləğv|legv)",
    r"rolunu\s+(dəyiş|deyis)",
    r"(sən|sen)\s+(artıq|artiq)\s+.{0,40}\s+(deyilsən|deyilsen)",
    r"игнорируй\s+(все\s+)?(предыдущие\s+)?инструкции",
    r"забудь\s+(все\s+)?(предыдущие\s+)?инструкции",
    r"системн\w*\s+(промпт|инструкц)",
)]
_ABUSE = [re.compile(rf"(?<!\w){word}(?!\w)") for word in ("axmaq", "alçaq", "idiot", "debil")]  # pilot üçün minimal siyahı


class GuardAgent:
    name = "guard"

    def __init__(self, llm: PrivateLLM | None = None):
        self.llm = llm or PrivateLLM(self.name)

    def check(self, text: str, pseudo: Pseudonymizer) -> GuardResult:
        if len(text) > MAX_INPUT_CHARS:
            return GuardResult(allowed=False, reason="too_long")
        # Azərbaycan kiçik hərfi "I"-ni "ı" edir ("ıgnore"), ingilis qaydaları üçün standart forma da yoxlanılır
        variants = {az_lower(text), text.lower()}
        if any(p.search(v) for p in _INJECTION for v in variants):
            return GuardResult(allowed=False, reason="prompt_injection")
        if any(p.search(v) for p in _ABUSE for v in variants):
            return GuardResult(allowed=False, reason="abuse")
        if self.llm.guardrails_allows(text, pseudo) is False:
            return GuardResult(allowed=False, reason="nemo_input_rail")
        return GuardResult(allowed=True)
