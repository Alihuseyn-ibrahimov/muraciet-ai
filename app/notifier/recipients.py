"""Bildirişlərin alıcıları: rola görə aktiv əməkdaşlar."""
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.tables import Staff
from app.workflow.states import BotKind, StaffRole


def active_staff(session: Session, role: StaffRole) -> list[Staff]:
    return list(session.scalars(select(Staff).where(Staff.role == role, Staff.is_active).order_by(Staff.id)))


def coordinators(session: Session) -> list[Staff]:
    return active_staff(session, StaffRole.coordinator)


def heads(session: Session) -> list[Staff]:
    return active_staff(session, StaffRole.head)


def admins(session: Session) -> list[Staff]:
    return list(session.scalars(select(Staff).where(Staff.is_admin, Staff.is_active).order_by(Staff.id)))


TARGETS = {"head": heads, "coordinators": coordinators, "admins": admins}


def broadcast(session: Session, target: str, text: str, *, now: datetime, calendar=None,
              only_workday: bool = False) -> int:
    """n8n-in mesajı (gündəlik hesabat, xəta bildirişi) əməkdaş botu ilə, outbox vasitəsilə.
    only_workday: bayram və istirahət günlərində göndərilmir (n8n-in cron-u yalnız həftə sonunu bilir)."""
    from app.notifier.cards import Rendered
    from app.notifier.outbox import enqueue_rendered

    if target not in TARGETS:
        raise ValueError(f"naməlum alıcı qrupu: {target}")
    if only_workday and not calendar.is_workday(now.astimezone(calendar.tz).date()):
        return 0
    people = TARGETS[target](session)
    if not people and target == "head":  # rəhbər hələ qeydiyyatda deyil: hesabat itməsin, adminə getsin
        people = admins(session)
    for person in people:
        enqueue_rendered(session, bot=BotKind.staff, chat_id=person.telegram_user_id, rendered=Rendered(text),
                         purpose="broadcast", now=now)
    return len(people)
