"""2. Marşrutlaşdırıcı: vətəndaşın mesajının niyyətini təyin edir. Xəta olsa, çağıran "question" qəbul edir."""
from dataclasses import dataclass

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.base import Agent, format_dialogue
from app.agents.schemas import Intent
from app.privacy.masking import Pseudonymizer


@dataclass(frozen=True)
class RouterContext:
    has_open_question: bool = False  # əməkdaşın vətəndaşa cavabsız sualı var


class RouterAgent(Agent[Intent]):
    name, schema = "router", Intent

    def classify(self, text: str, *, dialogue: list[tuple[str, str]], context: RouterContext,
                 pseudo: Pseudonymizer) -> Intent | None:
        facts = "Vətəndaşın əməkdaşdan gözləyən sualı var (cavabsız)." if context.has_open_question else "yoxdur"
        human = f"Kontekst: {facts}\n\nSon dialoq:\n{format_dialogue(dialogue)}\n\nYeni mesaj: {text}"
        return self.ask([SystemMessage(self.prompt()), HumanMessage(human)], pseudo)
