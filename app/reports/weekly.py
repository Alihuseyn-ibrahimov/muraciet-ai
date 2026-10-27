"""Rəhbərin həftəlik hesabatı: rəqəmləri SQL hesablayır, hesabat analitiki agenti (10) şərh edir.

Göstəricilərdə şəxsi məlumat yoxdur: saylar, şöbə adları və bilik bazasının boşluqları (maskalanmış suallar).
Agent alınmasa və ya uydurma rəqəm yazsa, hesabat AI təhlili olmadan göndərilir.
"""
import statistics
from collections.abc import Callable
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.agents.schemas import Analysis
from app.db.tables import Appeal, AppealEvent, ChatLog, Department
from app.knowledge.gaps import knowledge_gaps
from app.privacy.masking import Pseudonymizer
from app.workflow.states import CATEGORY_LABELS, AppealStatus

OPEN = (AppealStatus.new, AppealStatus.assigned, AppealStatus.waiting_citizen, AppealStatus.pending_approval)
GAPS_LIMIT = 5


def period_stats(s: Session, *, since: datetime, until: datetime, today: date, no_answer_text: str) -> dict:
    def count(*where) -> int:
        return s.scalar(select(func.count()).select_from(Appeal).where(*where)) or 0

    in_period = lambda column: (column >= since, column < until)  # noqa: E731
    hours = [float(h) for h in s.scalars(select(Appeal.answer_work_hours).where(
        *in_period(Appeal.answered_at), Appeal.answer_work_hours.is_not(None)))]
    feedback = dict(s.execute(select(Appeal.satisfaction, func.count()).where(
        *in_period(Appeal.answered_at), Appeal.satisfaction.is_not(None)).group_by(Appeal.satisfaction)).all())
    positive, negative = feedback.get(1, 0), feedback.get(-1, 0)

    departments = []
    for name, dept_id in s.execute(select(Department.name, Department.id).order_by(Department.name)):
        created = count(Appeal.department_id == dept_id, *in_period(Appeal.created_at))
        overdue = count(Appeal.department_id == dept_id, Appeal.status.in_(OPEN), Appeal.due_date < today)
        if created or overdue:
            departments.append({"department": name, "created": created, "overdue": overdue})
    categories = s.execute(select(Appeal.category, func.count()).where(
        *in_period(Appeal.created_at), Appeal.category.is_not(None)).group_by(Appeal.category)).all()

    return {
        "created": count(*in_period(Appeal.created_at)),
        "answered": count(*in_period(Appeal.answered_at)),
        "closed": count(*in_period(Appeal.closed_at)),
        "open_total": count(Appeal.status.in_(OPEN)),
        "overdue_total": count(Appeal.status.in_(OPEN), Appeal.due_date < today),
        "median_answer_work_hours": round(statistics.median(hours), 1) if hours else None,
        "positive": positive,
        "negative": negative,
        "satisfaction_percent": round(100 * positive / (positive + negative)) if positive + negative else None,
        "reopened": s.scalar(select(func.count()).select_from(AppealEvent).where(
            AppealEvent.action == "feedback_negative", *in_period(AppealEvent.created_at))) or 0,
        "ai_answers": s.scalar(select(func.count()).select_from(ChatLog).where(
            ChatLog.resolved_by_ai, *in_period(ChatLog.created_at))) or 0,
        "by_department": sorted(departments, key=lambda d: (-d["overdue"], -d["created"])),
        "by_category": {CATEGORY_LABELS[category]: n for category, n in sorted(categories, key=lambda r: -r[1])},
        "knowledge_gaps": knowledge_gaps(s, since=since, no_answer_text=no_answer_text, limit=GAPS_LIMIT),
    }


def _value(value, suffix: str = "") -> str:
    return "—" if value is None else f"{value}{suffix}"


def render_report(stats: dict, analysis: Analysis | None, *, period: tuple[date, date]) -> str:
    start, end = (d.strftime("%d.%m.%Y") for d in period)
    lines = [
        f"📈 Həftəlik hesabat · {start}–{end}",
        f"Daxil olub: {stats['created']} · Cavablandırılıb: {stats['answered']} · Bağlanıb: {stats['closed']}",
        f"Açıq: {stats['open_total']} (müddəti keçib: {stats['overdue_total']})",
        f"Median cavab vaxtı: {_value(stats['median_answer_work_hours'], ' iş saatı')}",
        f"👍 {stats['positive']} / 👎 {stats['negative']} (👍 payı: {_value(stats['satisfaction_percent'], '%')}) · "
        f"yenidən açılıb: {stats['reopened']}",
        f"AI-nin özü cavablandırdığı suallar: {stats['ai_answers']}",
    ]
    if stats["by_department"]:
        lines += ["", "Şöbələr (daxil olub / müddəti keçən açıq):"]
        lines += [f" • {d['department']}: {d['created']} / {d['overdue']}" for d in stats["by_department"]]
    if stats["by_category"]:
        lines += ["", "Kateqoriyalar: " + ", ".join(f"{k} {v}" for k, v in stats["by_category"].items())]
    if stats["knowledge_gaps"]:
        lines += ["", "AI-nin cavab tapmadığı suallar (bilik bazasına əlavə üçün):"]
        lines += [f" • {g['question']} ({g['count']})" for g in stats["knowledge_gaps"]]
    lines.append("")
    if analysis is None:
        lines.append("🤖 AI təhlili alınmadı, yalnız rəqəmlər göstərilir.")
    else:
        lines += ["🤖 Təhlil:", analysis.summary]
        lines += [f" • {h}" for h in analysis.highlights]
        if analysis.recommendations:
            lines += ["Tövsiyələr:", *[f" • {r}" for r in analysis.recommendations]]
    return "\n".join(lines)


def build_report(sessions: sessionmaker[Session], *, now: datetime, today: date, days: int, no_answer_text: str,
                 analyse: Callable[[dict, Pseudonymizer], Analysis | None]) -> tuple[str, dict, bool]:
    """(mətn, göstəricilər, AI təhlili var). LLM çağırışı DB sessiyasından kənardadır."""
    since = now - timedelta(days=days)
    with sessions() as s:
        stats = period_stats(s, since=since, until=now, today=today, no_answer_text=no_answer_text)
    analysis = analyse(stats, Pseudonymizer())
    period = (today - timedelta(days=days - 1), today)
    return render_report(stats, analysis, period=period), stats, analysis is not None
