"""Həftəlik hesabat: göstəricilər real PostgreSQL-də hesablanır, şəxsi məlumat daxil deyil."""
import json
from datetime import timedelta

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.agents.schemas import Analysis
from app.db.tables import ChatLog
from app.reports.weekly import build_report, period_stats
from app.workflow.service import AppealService, RoutingOutcome, TriageOutcome
from app.workflow.states import Category, Outcome, Priority
from tests.fakes import RecordingNotifier
from tests.integration.helpers import CAL, CFG, CITIZEN_TG, CRYPTO, MONDAY_10, Clock

pytestmark = pytest.mark.integration
NO_ANSWER = CFG.templates.render("no_answer_offer_appeal")


def run(app_engine, fn, clock=None):
    with Session(app_engine, expire_on_commit=False) as s, s.begin():
        return fn(AppealService(s, config=CFG, calendar=CAL, crypto=CRYPTO, notifier=RecordingNotifier(),
                                clock=clock or Clock(MONDAY_10)))


def appeal(app_engine, world, *, category=Category.request, department="pensiya", answer=None, feedback=None):
    created = run(app_engine, lambda svc: svc.create_appeal(
        world.citizen_id, subject="Pensiya köçmür", summary="Məmmədov Əli: pensiya köçmür", description="FİN 5ABC12D"))
    created = run(app_engine, lambda svc: svc.apply_triage(
        created.id, TriageOutcome(category, Priority.normal, False), RoutingOutcome(department, "x")))
    if answer is None:
        return created
    assigned = run(app_engine, lambda svc: svc.assign(world.coordinator, created.id, world.staff_a.id,
                                                      expected_version=created.version))
    answered = run(app_engine, lambda svc: svc.submit_answer(
        world.staff_a, assigned.id, outcome=Outcome.explained, text=answer, qa_critical=False,
        expected_version=assigned.version), Clock(MONDAY_10 + timedelta(days=1)))
    if feedback is not None:
        run(app_engine, lambda svc: svc.citizen_feedback(world.citizen_id, answered.id, satisfied=feedback))
    return answered


def test_weekly_stats_are_aggregates_without_personal_data(app_engine, world):
    appeal(app_engine, world, answer="Ödəniş bərpa olundu.", feedback=True)
    appeal(app_engine, world, answer="İzah edildi.", feedback=False)
    appeal(app_engine, world, category=Category.complaint, department="elillik")
    with Session(app_engine) as s, s.begin():
        s.add_all([ChatLog(citizen_telegram_id=CITIZEN_TG, role="citizen", content="Vergi nə qədərdir?",
                           intent="question", created_at=MONDAY_10),
                   ChatLog(citizen_telegram_id=CITIZEN_TG, role="bot", content=NO_ANSWER, intent="question",
                           created_at=MONDAY_10 + timedelta(seconds=1))])

    now = MONDAY_10 + timedelta(days=3)
    with Session(app_engine) as s:
        stats = period_stats(s, since=now - timedelta(days=7), until=now, today=now.date(), no_answer_text=NO_ANSWER)
    assert (stats["created"], stats["answered"], stats["open_total"]) == (3, 2, 2)  # 👎 yenidən açır
    assert (stats["positive"], stats["negative"], stats["satisfaction_percent"], stats["reopened"]) == (1, 1, 50, 1)
    assert stats["by_category"] == {"Ərizə": 2, "Şikayət": 1}
    names = {d.code: d.name for d in CFG.departments}
    assert stats["by_department"] == [{"department": names["pensiya"], "created": 2, "overdue": 0},
                                      {"department": names["elillik"], "created": 1, "overdue": 0}]
    assert stats["knowledge_gaps"] == [{"question": "Vergi nə qədərdir?", "count": 1}]
    dumped = json.dumps(stats, ensure_ascii=False)
    assert "Məmmədov" not in dumped and "5ABC12D" not in dumped


def test_build_report_falls_back_to_plain_numbers(app_engine, world):
    appeal(app_engine, world)
    factory = sessionmaker(app_engine, expire_on_commit=False)
    now = MONDAY_10 + timedelta(days=1)
    seen = []

    def analyse(stats, pseudo):
        seen.append(stats)
        return Analysis(summary=f"Həftədə {stats['created']} müraciət daxil olub.")

    text, stats, ai = build_report(factory, now=now, today=now.date(), days=7, no_answer_text=NO_ANSWER,
                                   analyse=analyse)
    assert ai and "Həftədə 1 müraciət daxil olub." in text and seen == [stats]
    text, _, ai = build_report(factory, now=now, today=now.date(), days=7, no_answer_text=NO_ANSWER,
                               analyse=lambda stats, pseudo: None)
    assert not ai and "Daxil olub: 1" in text and "AI təhlili alınmadı" in text
