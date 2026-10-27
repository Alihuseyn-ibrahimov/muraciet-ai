"""4. Qəbul agenti: problemi rəsmi müraciət üçün strukturlaşdırır.

Ən çoxu MAX_QUESTIONS sual verir və bu limit promptda deyil, kodda tətbiq olunur. Bu agent heç vaxt
None qaytarmır: LLM işləməsə, vətəndaşın öz mətni müraciətin təsviri kimi götürülür, ona görə
qeydiyyat LLM-siz də davam edir (spec 10-cu bölmə).
"""
from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.base import Agent, format_dialogue
from app.agents.schemas import IntakeResult
from app.privacy.masking import Pseudonymizer

MAX_QUESTIONS = 3


def _clean(text: str) -> str:
    """Model bəzən sahənin sonuna JSON-un qırıntısını ("}", dırnaq) qoyur, real yoxlamada kartda görünmüşdü."""
    return text.strip().rstrip('}{"\'`').strip()


def _subject_from(text: str) -> str:
    words = text.split()
    subject = " ".join(words[:8])
    return (subject[:97] + "...") if len(subject) > 100 else subject or "Müraciət"


class IntakeAgent(Agent[IntakeResult]):
    name, schema = "intake", IntakeResult

    def step(self, dialogue: list[tuple[str, str]], *, questions_asked: int, pseudo: Pseudonymizer) -> IntakeResult:
        citizen_text = " ".join(text for role, text in dialogue if role == "citizen").strip()
        result = self.ask([SystemMessage(self.prompt(asked=questions_asked, max_questions=MAX_QUESTIONS)),
                           HumanMessage(format_dialogue(dialogue))], pseudo)
        if result is None:
            return IntakeResult(complete=True, subject=_subject_from(citizen_text), summary=citizen_text[:2000])

        must_finish = questions_asked >= MAX_QUESTIONS or not (result.next_question or "").strip()
        if result.complete or must_finish:
            return result.model_copy(update={
                "complete": True,
                "next_question": None,
                "subject": _clean(result.subject) or _subject_from(citizen_text),
                "summary": _clean(result.summary) or citizen_text[:2000],
            })
        return result
