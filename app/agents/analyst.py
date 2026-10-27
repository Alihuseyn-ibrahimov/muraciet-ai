"""10. Hesabat analitiki agenti (spec 15, Mərhələ 3): rəhbər üçün həftəlik göstəricilərin qısa təhlili və tövsiyələr.

Rəqəmləri kod hesablayır (app/reports/weekly.py), agent yalnız şərh edir. Uydurmaya qarşı deterministik qoruma:
mətndəki hər rəqəm göstəricilərdə olmalıdır, əks halda təhlil atılır və hesabat AI-siz (yalnız rəqəmlərlə) gedir.
Girişdə şəxsi məlumat yoxdur: aqreqat saylar, şöbə adları və maskalanmış cavabsız suallar.
"""
import json
import re

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.base import Agent
from app.agents.schemas import Analysis
from app.privacy.masking import Pseudonymizer

_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def numbers_in(value: object) -> set[str]:
    """Mətndəki və ya strukturdakı bütün rəqəmlər, vergül nöqtəyə çevrilmiş halda ("20,5" → "20.5")."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return {match.replace(",", ".") for match in _NUMBER.findall(text)}


class AnalystAgent(Agent[Analysis]):
    name, schema = "analyst", Analysis

    def analyse(self, stats: dict, pseudo: Pseudonymizer) -> Analysis | None:
        data = json.dumps(stats, ensure_ascii=False, indent=1)
        result = self.ask([SystemMessage(self.prompt()), HumanMessage(f"Həftənin göstəriciləri (JSON):\n{data}")],
                          pseudo)
        if result is None or not result.summary.strip():
            return None
        allowed = numbers_in(stats)
        written = numbers_in(" ".join([result.summary, *result.highlights, *result.recommendations]))
        if not written <= allowed:
            return None
        return result
