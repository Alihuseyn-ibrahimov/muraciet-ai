"""5. Triaj agenti: kateqoriya, prioritet və hüquqi məsələ. None qaytararsa servis təhlükəsiz standarta keçir."""
from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.base import Agent
from app.agents.schemas import Triage
from app.privacy.masking import Pseudonymizer


class TriageAgent(Agent[Triage]):
    name, schema = "triage", Triage

    def classify(self, subject: str, summary: str, description: str, pseudo: Pseudonymizer) -> Triage | None:
        human = f"Mövzu: {subject}\nXülasə: {summary}\nTəsvir: {description}"
        return self.ask([SystemMessage(self.prompt()), HumanMessage(human)], pseudo)
