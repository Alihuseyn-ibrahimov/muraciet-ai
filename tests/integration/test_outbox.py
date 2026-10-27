"""Outbox: zəmanətli çatdırılma, təkrar cəhd, xəbərdarlıq, restart bərpası (spec 10-cu bölmə)."""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.tables import Appeal, Citizen, Outbox, Staff, TelegramMessage
from app.notifier.cards import Rendered, keyboard
from app.notifier.outbox import OutboxWorker, enqueue_edit, enqueue_rendered
from app.notifier.telegram_api import BadRequest, Forbidden, RetryAfter, TelegramUnavailable
from app.workflow.states import BotKind, OutboxStatus, StaffRole
from app.yaml_config import load_config
from tests.fakes import FakeTelegramClient

pytestmark = pytest.mark.integration

OUTBOX_CFG = load_config().sla.outbox
T0 = datetime(2026, 10, 19, 10, 0, tzinfo=timezone.utc)
CITIZEN_CHAT, COORD_A, COORD_B, COORD_OFF = 5001, 100, 101, 102


class Clock:
    def __init__(self, moment):
        self.moment = moment

    def __call__(self):
        return self.moment


@pytest.fixture
def factory(app_engine):
    return sessionmaker(app_engine, expire_on_commit=False)


@pytest.fixture
def appeal_id(factory) -> int:
    with factory() as s, s.begin():
        s.add_all([
            Staff(telegram_user_id=COORD_A, full_name="Koordinator A", role=StaffRole.coordinator),
            Staff(telegram_user_id=COORD_B, full_name="Koordinator B", role=StaffRole.coordinator),
            Staff(telegram_user_id=COORD_OFF, full_name="Deaktiv", role=StaffRole.coordinator, is_active=False),
        ])
        citizen = Citizen(telegram_user_id=CITIZEN_CHAT, last_name="M", first_name="Ə")
        s.add(citizen)
        s.flush()
        appeal = Appeal(citizen_id=citizen.id, number="MR-2026-000001", subject="x", summary="y", description="z",
                        due_date=date(2026, 11, 6), original_due_date=date(2026, 11, 6))
        s.add(appeal)
        s.flush()
        return appeal.id


def worker(factory, clock, citizen=None, staff=None) -> OutboxWorker:
    clients = {}
    if citizen is not None:
        clients[BotKind.citizen] = citizen
    if staff is not None:
        clients[BotKind.staff] = staff
    return OutboxWorker(factory, clients, config=OUTBOX_CFG, clock=clock)


def rows(factory) -> list[Outbox]:
    with factory() as s:
        return list(s.scalars(select(Outbox).order_by(Outbox.id)))


def enqueue_citizen_message(factory, appeal_id, text="Rəsmi cavab", purpose="citizen_answer"):
    with factory() as s, s.begin():
        enqueue_rendered(s, bot=BotKind.citizen, chat_id=CITIZEN_CHAT, rendered=Rendered(text),
                         appeal_id=appeal_id, purpose=purpose, now=T0)


def test_success_marks_sent_and_records_message_mapping(factory, appeal_id):
    markup = keyboard([("OK", "ap:1:1")])
    with factory() as s, s.begin():
        enqueue_rendered(s, bot=BotKind.staff, chat_id=COORD_A, rendered=Rendered("Kart", markup),
                         appeal_id=appeal_id, purpose="coordinator_card", now=T0)
    staff = FakeTelegramClient()
    assert worker(factory, Clock(T0), staff=staff).process_batch() == 1
    assert staff.sent == [("send_message", COORD_A, {"text": "Kart", "reply_markup": markup})]
    row = rows(factory)[0]
    assert row.status == OutboxStatus.sent and row.sent_at == T0
    with factory() as s:
        mapping = s.scalar(select(TelegramMessage))
        assert (mapping.chat_id, mapping.message_id, mapping.purpose, mapping.appeal_id) == \
            (COORD_A, 1001, "coordinator_card", appeal_id)


def test_long_text_becomes_several_rows_with_buttons_on_last(factory, appeal_id):
    with factory() as s, s.begin():
        enqueue_rendered(s, bot=BotKind.citizen, chat_id=CITIZEN_CHAT,
                         rendered=Rendered("ə" * 9000, keyboard([("👍", "fp:1")])), appeal_id=appeal_id,
                         purpose="citizen_answer")
    found = rows(factory)
    assert len(found) == 3
    assert [r.payload.get("reply_markup") is not None for r in found] == [False, False, True]


def test_retry_schedule_then_dead_with_alert_to_active_coordinators(factory, appeal_id):
    enqueue_citizen_message(factory, appeal_id)
    clock = Clock(T0)
    citizen = FakeTelegramClient([TelegramUnavailable("şəbəkə")] * 6)
    w = worker(factory, clock, citizen=citizen, staff=FakeTelegramClient())
    delays = []
    for _ in range(5):
        w.process_batch()
        row = rows(factory)[0]
        assert row.status == OutboxStatus.pending
        delays.append(int((row.next_attempt_at - clock.moment).total_seconds()))
        clock.moment = row.next_attempt_at
    assert delays == OUTBOX_CFG.retry_delays_seconds  # 1 dəq, 5 dəq, 15 dəq, 1 saat, 4 saat

    w.process_batch()  # 6-cı cəhd
    original, *alerts = rows(factory)
    assert original.status == OutboxStatus.dead and "şəbəkə" in original.last_error
    assert sorted(a.chat_id for a in alerts) == [COORD_A, COORD_B]  # deaktiv koordinatora getmir
    assert all(a.purpose == "delivery_alert" and "MR-2026-000001" in a.payload["text"] for a in alerts)


def test_rate_limit_waits_without_consuming_attempts(factory, appeal_id):
    enqueue_citizen_message(factory, appeal_id)
    worker(factory, Clock(T0), citizen=FakeTelegramClient([RetryAfter(7)])).process_batch()
    row = rows(factory)[0]
    assert row.status == OutboxStatus.pending and row.attempts == 0
    assert row.next_attempt_at == T0 + timedelta(seconds=7)


def test_blocked_bot_is_dead_immediately_with_alert(factory, appeal_id):
    enqueue_citizen_message(factory, appeal_id)
    worker(factory, Clock(T0), citizen=FakeTelegramClient([Forbidden("bot was blocked by the user")])).process_batch()
    original, *alerts = rows(factory)
    assert original.status == OutboxStatus.dead and original.attempts == 1
    assert len(alerts) == 2 and "telefonla" in alerts[0].payload["text"]


def test_failed_alert_does_not_trigger_another_alert(factory, appeal_id):
    with factory() as s, s.begin():
        enqueue_rendered(s, bot=BotKind.staff, chat_id=COORD_A, rendered=Rendered("⚠️ xəbərdarlıq"),
                         appeal_id=appeal_id, purpose="delivery_alert", now=T0)
    worker(factory, Clock(T0), staff=FakeTelegramClient([Forbidden("blocked")])).process_batch()
    assert [r.status for r in rows(factory)] == [OutboxStatus.dead]


def test_failed_card_edit_is_dead_without_alert(factory, appeal_id):
    with factory() as s, s.begin():
        enqueue_edit(s, bot=BotKind.staff, chat_id=COORD_A, message_id=55, rendered=Rendered("bağlı kart"),
                     appeal_id=appeal_id, purpose="coordinator_card", now=T0)
    worker(factory, Clock(T0), staff=FakeTelegramClient([BadRequest("message to edit not found")])).process_batch()
    assert [r.status for r in rows(factory)] == [OutboxStatus.dead]


def test_stale_sending_row_is_recovered_after_restart(factory, appeal_id):
    enqueue_citizen_message(factory, appeal_id)
    with factory() as s, s.begin():  # worker göndərmə ortasında çöküb
        s.execute(text("UPDATE outbox SET status = 'sending', next_attempt_at = :t"),
                  {"t": T0 - timedelta(minutes=OUTBOX_CFG.stale_sending_minutes + 1)})
    citizen = FakeTelegramClient()
    worker(factory, Clock(T0), citizen=citizen).process_batch()
    assert rows(factory)[0].status == OutboxStatus.sent and len(citizen.sent) == 1


def test_recent_sending_row_is_not_touched(factory, appeal_id):
    enqueue_citizen_message(factory, appeal_id)
    with factory() as s, s.begin():  # başqa worker indicə göndərir
        s.execute(text("UPDATE outbox SET status = 'sending', next_attempt_at = :t"), {"t": T0})
    citizen = FakeTelegramClient()
    worker(factory, Clock(T0), citizen=citizen).process_batch()
    assert rows(factory)[0].status == OutboxStatus.sending and citizen.sent == []


def test_claims_skip_rows_locked_by_another_worker(factory, appeal_id):
    for i in range(3):
        enqueue_citizen_message(factory, appeal_id, text=f"mesaj {i}")
    with Session(factory.kw["bind"]) as other, other.begin():  # başqa worker 1-ci sətri tutub
        locked_id = other.execute(
            text("SELECT id FROM outbox ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED")).scalar()
        claimed = worker(factory, Clock(T0)).claim(limit=10)
        assert locked_id not in claimed and len(claimed) == 2


def test_missing_bot_token_is_retried(factory, appeal_id):
    enqueue_citizen_message(factory, appeal_id)
    worker(factory, Clock(T0)).process_batch()  # vətəndaş botunun klienti yoxdur
    row = rows(factory)[0]
    assert row.status == OutboxStatus.pending and "token" in row.last_error


# ---------- gizli mesajlar (dəvət linki) ----------
def test_secret_message_text_is_erased_after_sending(factory, appeal_id):
    from app.notifier.outbox import REDACTED, SECRET_PURPOSE

    with factory() as s, s.begin():
        enqueue_rendered(s, bot=BotKind.staff, chat_id=COORD_A, rendered=Rendered("link ?start=inv_TOKEN123"),
                         purpose=SECRET_PURPOSE, now=T0)
    staff = FakeTelegramClient()
    worker(factory, Clock(T0), staff=staff).process_batch()
    assert staff.sent == [("send_message", COORD_A, {"text": "link ?start=inv_TOKEN123", "reply_markup": None})]  # əməkdaş linki alıb
    row = rows(factory)[0]
    assert row.status == OutboxStatus.sent and row.payload == {"text": REDACTED}
    with factory() as s:
        assert s.scalar(select(TelegramMessage)) is None  # kart deyil, yenilənmir


def test_secret_message_text_is_erased_when_delivery_fails(factory, appeal_id):
    from app.notifier.outbox import REDACTED, SECRET_PURPOSE

    with factory() as s, s.begin():
        enqueue_rendered(s, bot=BotKind.staff, chat_id=COORD_A, rendered=Rendered("?start=inv_TOKEN123"),
                         purpose=SECRET_PURPOSE, now=T0)
    worker(factory, Clock(T0), staff=FakeTelegramClient([Forbidden("bloklanıb")] * 3)).process_batch()
    row = rows(factory)[0]
    assert row.status == OutboxStatus.dead and "TOKEN123" not in str(row.payload)


# ---------- əlavələr (send_document) ----------
def test_attachment_is_decrypted_and_uploaded_only_at_sending_time(factory, appeal_id):
    from app.db.tables import Attachment
    from app.notifier.outbox import enqueue_document
    from tests.integration.helpers import CRYPTO

    with factory() as s, s.begin():
        row = Attachment(owner_telegram_id=CITIZEN_CHAT, appeal_id=appeal_id, kind="document", mime="application/pdf",
                         size=8, name_enc=CRYPTO.encrypt("arayış.pdf"), content_enc=CRYPTO.encrypt_bytes(b"%PDF-1.4"))
        s.add(row)
        s.flush()
        enqueue_document(s, bot=BotKind.staff, chat_id=COORD_A, attachment_id=row.id, caption="MR-2026-000001",
                         appeal_id=appeal_id, now=T0)
    assert "PDF" not in str(rows(factory)[0].payload)  # növbədə məzmun yoxdur, yalnız istinad
    staff = FakeTelegramClient()
    OutboxWorker(factory, {BotKind.staff: staff}, config=OUTBOX_CFG, clock=Clock(T0), crypto=CRYPTO).process_batch()
    assert staff.sent == [("send_document", COORD_A, {"filename": "arayış.pdf", "content": b"%PDF-1.4",
                                                       "caption": "MR-2026-000001"})]
    assert rows(factory)[0].status == OutboxStatus.sent


def test_missing_attachment_goes_dead_without_retries(factory, appeal_id):
    from app.notifier.outbox import enqueue_document
    from tests.integration.helpers import CRYPTO

    with factory() as s, s.begin():
        enqueue_document(s, bot=BotKind.staff, chat_id=COORD_A, attachment_id=999999, caption="x", now=T0)
    OutboxWorker(factory, {BotKind.staff: FakeTelegramClient()}, config=OUTBOX_CFG, clock=Clock(T0),
                 crypto=CRYPTO).process_batch()
    assert rows(factory)[0].status == OutboxStatus.dead
