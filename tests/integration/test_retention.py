"""Saxlama müddəti təmizliyi (spec 9-cu bölmə, plan 8.5): şəxsi sahələr silinir, statistika qalır."""
import logging
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.tables import Appeal, AppealMessage, Attachment, ChatLog, Citizen, LiveChat, Outbox
from app.workflow.retention import REDACTED, retention_cleanup
from app.workflow.states import AppealStatus, BotKind, Category, MessageKind, OutboxStatus
from tests.integration.helpers import BAKU, CITIZEN_TG, OTHER_CITIZEN_TG

pytestmark = pytest.mark.integration
NOW = datetime(2030, 3, 1, 12, tzinfo=BAKU)
OLD = NOW - timedelta(days=4 * 365)  # 3 illik müddətdən köhnə
RECENT = NOW - timedelta(days=30)


def appeal(s: Session, citizen_id: int, *, status: AppealStatus, closed_at: datetime | None, number: str) -> Appeal:
    row = Appeal(citizen_id=citizen_id, number=number, subject="Pensiya köçmür", summary="Məmmədov Əli yazır",
                 description="Nizami küç. 5, pensiya", status=status, category=Category.complaint,
                 due_date=(closed_at or NOW).date(), original_due_date=(closed_at or NOW).date(),
                 created_at=(closed_at or NOW) - timedelta(days=10), closed_at=closed_at, version=1)
    s.add(row)
    s.flush()
    s.add(AppealMessage(appeal_id=row.id, kind=MessageKind.final_answer, text="Hörmətli Əli Məmmədov, ...",
                        meta={"outcome": "satisfied"}))
    return row


@pytest.fixture
def data(app_engine, world):
    with Session(app_engine, expire_on_commit=False) as s, s.begin():
        old = appeal(s, world.citizen_id, status=AppealStatus.closed, closed_at=OLD, number="MR-2026-000001")
        open_ = appeal(s, world.other_citizen_id, status=AppealStatus.assigned, closed_at=None, number="MR-2030-000002")
        s.flush()
        file = dict(kind="photo", mime="image/jpeg", size=1, name_enc="x", content_enc=b"x")
        s.add_all([
            Attachment(owner_telegram_id=CITIZEN_TG, appeal_id=old.id, created_at=OLD, **file),
            Attachment(owner_telegram_id=OTHER_CITIZEN_TG, appeal_id=open_.id, created_at=OLD, **file),
            Attachment(owner_telegram_id=9999, appeal_id=None, created_at=NOW - timedelta(days=3), **file),  # atılıb
            Attachment(owner_telegram_id=9998, appeal_id=None, created_at=NOW - timedelta(hours=1), **file),  # yazır
            LiveChat(citizen_telegram_id=CITIZEN_TG, status="closed", created_at=OLD, closed_at=OLD),
            LiveChat(citizen_telegram_id=OTHER_CITIZEN_TG, status="closed", created_at=RECENT, closed_at=RECENT),
            ChatLog(citizen_telegram_id=CITIZEN_TG, role="citizen", content="Mən Əli Məmmədov", created_at=OLD),
            ChatLog(citizen_telegram_id=9999, role="citizen", content="Təzə sual", created_at=RECENT),
            Outbox(bot=BotKind.staff, chat_id=100, method="send_message", payload={"text": "Telefon: +994501234567"},
                   status=OutboxStatus.sent, next_attempt_at=OLD, created_at=OLD),
            Outbox(bot=BotKind.citizen, chat_id=CITIZEN_TG, method="send_message", payload={"text": "təzə"},
                   status=OutboxStatus.pending, next_attempt_at=RECENT, created_at=RECENT),
        ])
        s.execute(text("UPDATE citizens SET updated_at = :t, created_at = :t WHERE id = :id"),
                  {"t": OLD, "id": world.citizen_id})
    return {"old": old.id, "open": open_.id}


def test_cleanup_removes_personal_data_and_keeps_statistics(app_engine, world, data):
    summary = retention_cleanup(app_engine, now=NOW, years=3)
    assert summary == {"appeals": 1, "citizens": 1, "chat_logs": 1, "outbox": 1, "checkpoints": 0,
                       "attachments": 1, "orphan_attachments": 1, "live_chats": 1}
    with Session(app_engine) as s:
        old = s.get(Appeal, data["old"])
        assert (old.subject, old.summary, old.description) == (REDACTED, REDACTED, REDACTED)
        assert (old.status, old.category, old.number) == (AppealStatus.closed, Category.complaint, "MR-2026-000001")
        assert s.scalar(select(AppealMessage.text).where(AppealMessage.appeal_id == data["old"])) == REDACTED
        citizen = s.get(Citizen, world.citizen_id)
        assert (citizen.last_name, citizen.fin_enc, citizen.phone_enc, citizen.address_enc) == (None, None, None, None)
        assert citizen.telegram_user_id != CITIZEN_TG
        # açıq müraciəti olan vətəndaş və təzə məlumat toxunulmaz qalır
        assert s.get(Appeal, data["open"]).summary == "Məmmədov Əli yazır"
        assert s.scalar(select(Citizen.last_name).where(Citizen.telegram_user_id == OTHER_CITIZEN_TG)) == "Məmmədov"
        assert [c.content for c in s.scalars(select(ChatLog))] == ["Təzə sual"]
        assert s.scalar(select(Outbox.payload["text"].astext)) == "təzə"
        assert sorted(s.scalars(select(Attachment.owner_telegram_id))) == sorted([9998, OTHER_CITIZEN_TG])
        assert list(s.scalars(select(LiveChat.citizen_telegram_id))) == [OTHER_CITIZEN_TG]


def test_cleanup_is_idempotent(app_engine, world, data):
    retention_cleanup(app_engine, now=NOW, years=3)
    assert retention_cleanup(app_engine, now=NOW, years=3) == {
        "appeals": 0, "citizens": 0, "chat_logs": 0, "outbox": 0, "checkpoints": 0, "attachments": 0,
        "orphan_attachments": 0, "live_chats": 0}


def test_without_a_retention_period_nothing_is_deleted(app_engine, world, data, caplog):
    with caplog.at_level(logging.WARNING):
        # atılmış əlavə (müraciətə çevrilməyib) saxlama müddətindən asılı olmayaraq silinir
        assert retention_cleanup(app_engine, now=NOW, years=None) == {"skipped": True, "orphan_attachments": 1}
    assert "RETENTION_YEARS" in caplog.text
    with Session(app_engine) as s:
        assert s.get(Appeal, data["old"]).summary == "Məmmədov Əli yazır"


def test_a_citizen_who_chatted_recently_is_not_erased(app_engine, world, data):
    with Session(app_engine) as s, s.begin():
        s.add(ChatLog(citizen_telegram_id=CITIZEN_TG, role="citizen", content="Yenə yazıram", created_at=RECENT))
    retention_cleanup(app_engine, now=NOW, years=3)
    with Session(app_engine) as s:
        assert s.get(Citizen, world.citizen_id).telegram_user_id == CITIZEN_TG
        assert s.get(Appeal, data["old"]).summary == REDACTED  # köhnə müraciətin mətni yenə silinir
