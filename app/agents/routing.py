"""6. Yönləndirmə agenti: şöbəni səlahiyyət təsvirlərinə görə seçir.

Siyahıda olmayan kod gələrsə None qaytarır və servis müraciəti Ümumi şöbəyə göndərir. Model
uydurduğu şöbəyə müraciət yönləndirə bilməz.
"""
from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.base import Agent
from app.agents.schemas import Routing
from app.privacy.masking import Pseudonymizer
from app.yaml_config import Department


class RoutingAgent(Agent[Routing]):
    name, schema = "routing", Routing

    def route(self, subject: str, summary: str, departments: list[Department], pseudo: Pseudonymizer) -> Routing | None:
        active = [d for d in departments if d.active]
        listing = "\n".join(f"- {d.code}: {d.name}. Səlahiyyəti: {d.competence}" for d in active)
        result = self.ask([SystemMessage(self.prompt(departments=listing)),
                           HumanMessage(f"Mövzu: {subject}\nXülasə: {summary}")], pseudo)
        if result is None or result.department_code not in {d.code for d in active}:
            return None
        return result
