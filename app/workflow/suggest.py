"""İcraçı təklifi (spec 5-ci bölmə: agentə verilməyən iş). Sadə hesabdır, ona görə LLM lazım deyil:
şöbədə açıq işi ən az olan aktiv əməkdaş seçilir. Bərabərlik olanda son təyinatı ən köhnə olan
seçilir ki, iş növbə ilə paylansın."""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.tables import Appeal, Staff
from app.workflow.states import AppealStatus, StaffRole

ACTIVE_WORK = (AppealStatus.assigned, AppealStatus.waiting_citizen, AppealStatus.pending_approval)


def _open_count():
    return (
        select(func.count(Appeal.id))
        .where(Appeal.assigned_staff_id == Staff.id, Appeal.status.in_(ACTIVE_WORK))
        .correlate(Staff)
        .scalar_subquery()
    )


def suggest_assignee(session: Session, department_id: int) -> Staff | None:
    return session.scalar(
        select(Staff)
        .where(Staff.department_id == department_id, Staff.is_active, Staff.role == StaffRole.staff)
        .order_by(_open_count().asc(), Staff.last_assigned_at.asc().nulls_first(), Staff.id.asc())
        .limit(1)
    )


def open_appeal_count(session: Session, staff_id: int) -> int:
    return session.scalar(
        select(func.count(Appeal.id)).where(Appeal.assigned_staff_id == staff_id, Appeal.status.in_(ACTIVE_WORK))
    )
