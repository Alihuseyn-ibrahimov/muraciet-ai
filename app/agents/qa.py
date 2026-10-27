"""8. Keyfiyyət agenti: əməkdaşın cavabını göndərilməzdən əvvəl yoxlayır. Cavabı düzəltmir.

Kritik yoxlamalar deterministikdir, LLM-ə etibar edilmir və LLM xəta versə də işləyir:
- pii_other_person: cavabda vətəndaşın özünə aid olmayan telefon, FİN, e-poçt və ya kart var;
- missing_appeal_procedure: rədd və ya qismən təmin cavabında şikayət qaydası yoxdur (m. 7.8).
LLM yalnız məzmun yoxlamalarını edir (m. 7.7, ziddiyyət, ton, orfoqrafiya). Onun qeydlərinin
ağırlığını kod müəyyən edir: model bir qeydi "kritik" edə bilməz.
"""
import logging
from collections.abc import Callable

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.base import Agent
from app.agents.schemas import QaIssue, QaReport
from app.knowledge.kb import KnowledgeHit, format_hits
from app.privacy.masking import Pseudonymizer, az_lower
from app.workflow.states import OUTCOME_LABELS, Outcome
from app.yaml_config import get_config

log = logging.getLogger(__name__)
LLM_SEVERITY = {
    "unanswered_question": "warning", "missing_measures": "warning", "contradicts_kb": "warning",
    "tone": "info", "spelling": "info",
}


def _mentions_appeal_procedure(answer: str) -> bool:
    if get_config().templates.texts["appeal_procedure"] in answer:
        return True
    lowered = az_lower(answer)
    return "şikayət" in lowered and ("məhkəmə" in lowered or "yuxarı" in lowered)


class QaAgent(Agent[QaReport]):
    name, schema = "qa", QaReport

    def review(self, *, appeal_summary: str, answer: str, outcome: Outcome, pseudo: Pseudonymizer,
               search: Callable[[str], list[KnowledgeHit]]) -> QaReport:
        issues = self._deterministic(answer, outcome, pseudo)
        try:
            hits = search(pseudo.mask(answer[:500]))
        except Exception as e:
            log.warning("qa: axtarış alınmadı (%s)", type(e).__name__)
            hits = []
        result = self.ask([
            SystemMessage(self.prompt(sources=format_hits(hits) or "yoxdur")),
            HumanMessage(f"Müraciətin xülasəsi: {appeal_summary}\nNəticə: {OUTCOME_LABELS[outcome]}\n\n"
                         f"Əməkdaşın cavabı:\n{answer}"),
        ], pseudo)
        if result is None:
            return QaReport(issues=issues, complete=False)
        llm_issues = [issue.model_copy(update={"severity": LLM_SEVERITY[issue.code]})
                      for issue in result.issues if issue.code in LLM_SEVERITY]
        return QaReport(issues=issues + llm_issues, complete=True)

    @staticmethod
    def _deterministic(answer: str, outcome: Outcome, pseudo: Pseudonymizer) -> list[QaIssue]:
        issues = []
        if pseudo.foreign_values(answer):
            issues.append(QaIssue(severity="critical", code="pii_other_person",
                                  message="Cavabda başqa şəxsin telefon, FİN, e-poçt və ya kart məlumatı var"))
        if outcome in (Outcome.rejected, Outcome.partial) and not _mentions_appeal_procedure(answer):
            issues.append(QaIssue(severity="critical", code="missing_appeal_procedure",
                                  message="Şikayət qaydası izah olunmayıb (m. 7.8)"))
        return issues
