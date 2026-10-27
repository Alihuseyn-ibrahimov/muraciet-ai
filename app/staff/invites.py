"""Əməkdaş dəvətləri (spec 7-ci bölmə): birdəfəlik, 24 saatlıq link. Bazada tokenin yalnız SHA-256 heşi saxlanılır.

Dəvəti admin əməkdaş botunda (/devet), ilk admini isə terminalda yaradır:
    python -m app.manage invite coordinator --admin
"""
import hashlib
import secrets
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.db.tables import StaffInvite
from app.workflow.states import StaffRole

INVITE_HOURS = 24


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_invite(session: Session, *, role: StaffRole, department_id: int | None, is_admin: bool,
                  created_by_staff_id: int | None, now: datetime) -> str:
    token = secrets.token_urlsafe(16)
    session.add(StaffInvite(token_hash=token_hash(token), role=role, is_admin=is_admin, department_id=department_id,
                            created_by_staff_id=created_by_staff_id, expires_at=now + timedelta(hours=INVITE_HOURS)))
    return token


def invite_link(bot_username: str, token: str) -> str:
    return f"https://t.me/{bot_username}?start=inv_{token}"
