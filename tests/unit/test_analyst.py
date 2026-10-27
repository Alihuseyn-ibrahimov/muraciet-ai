"""10. Hesabat analitiki: rəhbər üçün həftəlik təhlil. Mətndəki hər rəqəm hesablanmış göstəricilərdən olmalıdır."""
from datetime import date

import pytest

from app.agents.analyst import AnalystAgent, numbers_in
from app.privacy.llm import PrivateLLM
from app.privacy.masking import Pseudonymizer
from app.reports.weekly import render_report
from tests.fakes import FakeChatModel

pytestmark = pytest.mark.unit

STATS = {
    "created": 42, "answered": 35, "closed": 30, "open_total": 18, "overdue_total": 3,
    "median_answer_work_hours": 20.5, "positive": 12, "negative": 2, "satisfaction_percent": 86, "reopened": 2,
    "ai_answers": 57,
    "by_department": [{"department": "Pensiya", "created": 20, "overdue": 2},
                      {"department": "Əlillik", "created": 9, "overdue": 1}],
    "by_category": {"Şikayət": 25, "Ərizə": 17},
    "knowledge_gaps": [{"question": "Avtomobil vergisi nə qədərdir?", "count": 4}],
}
GOOD = {"summary": "Həftədə 42 müraciət daxil olub, 35-i cavablandırılıb.",
        "highlights": ["Müddəti keçən 3 müraciətin 2-si Pensiya şöbəsindədir.", "👍 payı 86% təşkil edir."],
        "recommendations": ["Pensiya şöbəsinin gecikən işlərinə baxılsın.",
                            "\"Avtomobil vergisi\" sualı 4 dəfə verilib: bilik bazasına əlavə edilsin."]}


def analyst(*script):
    spy = FakeChatModel(script=list(script))
    return AnalystAgent(PrivateLLM("analyst", model=spy)), spy


def test_analysis_uses_only_numbers_from_the_data():
    agent, spy = analyst(GOOD)
    result = agent.analyse(STATS, Pseudonymizer())
    assert result.summary == GOOD["summary"] and len(result.recommendations) == 2
    assert "42" in spy.all_text() and "Pensiya" in spy.all_text()


@pytest.mark.parametrize("invented", [
    "Həftədə 45 müraciət daxil olub.",  # uydurma rəqəm
    "Cavab müddəti 15% azalıb.",  # məlumatda olmayan faiz
    "Ötən həftə ilə müqayisədə 7 müraciət çoxdur.",
])
def test_analysis_with_invented_numbers_is_rejected(invented):
    agent, _ = analyst(GOOD | {"summary": invented})
    assert agent.analyse(STATS, Pseudonymizer()) is None


def test_number_extraction():
    assert numbers_in("42 müraciət, 20,5 saat, 86%, 2-si") == {"42", "20.5", "86", "2"}


def test_llm_failure_gives_none():
    agent, _ = analyst(RuntimeError("API"))
    assert agent.analyse(STATS, Pseudonymizer()) is None


def test_report_text_with_and_without_ai():
    agent, _ = analyst(GOOD)
    period = (date(2026, 10, 15), date(2026, 10, 21))
    text = render_report(STATS, agent.analyse(STATS, Pseudonymizer()), period=period)
    assert "15.10.2026–21.10.2026" in text and "Daxil olub: 42" in text and "Pensiya" in text
    assert GOOD["summary"] in text and GOOD["recommendations"][0] in text
    plain = render_report(STATS, None, period=period)
    assert "Daxil olub: 42" in plain and "AI təhlili alınmadı" in plain and GOOD["summary"] not in plain
