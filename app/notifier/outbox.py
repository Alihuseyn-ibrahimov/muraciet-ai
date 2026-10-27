"""Outbox (spec 4-cü və 10-cu bölmələr): hər Telegram mesajı əvvəlcə bazaya yazılır, sonra worker göndərir.

Mesaj vəziyyət dəyişikliyi ilə eyni tranzaksiyada yazılır. Tranzaksiya alınmasa mesaj da yoxdur,
alınsa mesaj itmir: Telegram, şəbəkə və ya worker çöksə də göndəriləcək.

Worker-in addımları:
1. ilişib qalan "sending" sətirlərini bərpa edir (worker göndərmə ortasında çöküb);
2. vaxtı çatmış sətirləri FOR UPDATE SKIP LOCKED ilə götürür (bir neçə worker eyni mesajı götürmür);
3. hər birini DB tranzaksiyasından kənarda göndərir (şəbəkə gözləməsi DB kilidi saxlamasın);
4. nəticəni yazır: göndərildi / təkrar cəhd / ölü + koordinatorlara xəbərdarlıq.

Çatdırılma zəmanəti "ən azı bir dəfə"dir. Worker göndərib statusu yaza bilməzdən əvvəl çöksə,
mesaj nadir hallarda təkrar gedə bilər. Bu, mesajın itməsindən daha yaxşıdır.
"""
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.db.tables import Appeal, Attachment, Outbox, TelegramMessage
from app.notifier import cards, recipients
from app.notifier.cards import TELEGRAM_LIMIT, Rendered, split_rendered
from app.notifier.telegram_api import BadRequest, Forbidden, RetryAfter, TelegramError, TelegramUnavailable
from app.privacy.crypto import Crypto
from app.workflow.states import BotKind, OutboxStatus
from app.yaml_config import OutboxConfig

log = logging.getLogger(__name__)
ALERT_PURPOSE = "delivery_alert"
# Gizli mesaj (dəvət linki): göndəriləndən və ya alınmayandan sonra mətni bazada qalmır
SECRET_PURPOSE, REDACTED = "secret", "[gizli mətn silinib]"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Sender(Protocol):
    def send_message(self, chat_id: int, text: str, *, reply_markup: dict | None = None) -> int: ...
    def edit_message_text(self, chat_id: int, message_id: int, text: str, *, reply_markup: dict | None = None) -> None: ...
    def send_document(self, chat_id: int, filename: str, content: bytes, *, caption: str | None = None) -> int: ...


# ---------- növbəyə yazmaq (servisin tranzaksiyası daxilində) ----------
def enqueue_rendered(session: Session, *, bot: BotKind, chat_id: int, rendered: Rendered,
                     appeal_id: int | None = None, purpose: str | None = None,
                     now: datetime | None = None) -> list[Outbox]:
    moment = now or utc_now()
    rows = []
    for part in split_rendered(rendered):
        payload = {"text": part.text}
        if part.reply_markup is not None:
            payload["reply_markup"] = part.reply_markup
        rows.append(Outbox(bot=bot, chat_id=chat_id, method="send_message", payload=payload,
                           appeal_id=appeal_id, purpose=purpose, next_attempt_at=moment))
    session.add_all(rows)
    return rows


def enqueue_edit(session: Session, *, bot: BotKind, chat_id: int, message_id: int, rendered: Rendered,
                 appeal_id: int | None = None, purpose: str | None = None,
                 now: datetime | None = None) -> Outbox:
    payload = {"message_id": message_id, "text": rendered.text[:TELEGRAM_LIMIT]}
    if rendered.reply_markup is not None:
        payload["reply_markup"] = rendered.reply_markup
    row = Outbox(bot=bot, chat_id=chat_id, method="edit_message_text", payload=payload, appeal_id=appeal_id,
                 purpose=purpose, next_attempt_at=now or utc_now())
    session.add(row)
    return row


def enqueue_document(session: Session, *, bot: BotKind, chat_id: int, attachment_id: int, caption: str,
                     appeal_id: int | None = None, now: datetime | None = None) -> Outbox:
    """Vətəndaşın əlavəsi. Növbədə yalnız istinad var: fayl göndərmə anında açılır və bazadan oxunur."""
    row = Outbox(bot=bot, chat_id=chat_id, method="send_document",
                 payload={"attachment_id": attachment_id, "caption": caption}, appeal_id=appeal_id,
                 next_attempt_at=now or utc_now())
    session.add(row)
    return row


# ---------- worker ----------
@dataclass(frozen=True)
class _Item:
    id: int
    bot: BotKind
    chat_id: int
    method: str
    payload: dict
    document: tuple[str, bytes] | None = None  # send_document: (fayl adı, açılmış məzmun), yalnız yaddaşda


class OutboxWorker:
    def __init__(self, session_factory: sessionmaker[Session], clients: Mapping[BotKind, Sender], *,
                 config: OutboxConfig, clock: Callable[[], datetime] = utc_now, crypto: Crypto | None = None):
        self._sessions = session_factory
        self._clients = clients
        self._crypto = crypto
        self._config = config
        self._clock = clock

    def process_batch(self, limit: int = 20) -> int:
        self.recover_stale()
        claimed = self.claim(limit)
        for outbox_id in claimed:
            self._deliver(outbox_id)
        return len(claimed)

    def recover_stale(self) -> int:
        cutoff = self._clock() - timedelta(minutes=self._config.stale_sending_minutes)
        with self._sessions() as s, s.begin():
            result = s.execute(
                update(Outbox)
                .where(Outbox.status == OutboxStatus.sending, Outbox.next_attempt_at < cutoff)
                .values(status=OutboxStatus.pending)
            )
            return result.rowcount

    def claim(self, limit: int) -> list[int]:
        now = self._clock()
        with self._sessions() as s, s.begin():
            rows = s.scalars(
                select(Outbox)
                .where(Outbox.status == OutboxStatus.pending, Outbox.next_attempt_at <= now)
                .order_by(Outbox.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).all()
            for row in rows:
                row.status = OutboxStatus.sending
                row.attempts += 1
                row.next_attempt_at = now  # göndərmənin başladığı an (ilişib qalanı tanımaq üçün)
            return [row.id for row in rows]

    def _deliver(self, outbox_id: int) -> None:
        with self._sessions() as s:
            row = s.get(Outbox, outbox_id)
            item = _Item(row.id, row.bot, row.chat_id, row.method, dict(row.payload))
            attachment = s.get(Attachment, item.payload["attachment_id"]) if item.method == "send_document" else None
            if attachment is not None and self._crypto is not None:
                item = _Item(item.id, item.bot, item.chat_id, item.method, item.payload,
                             (self._crypto.decrypt(attachment.name_enc),
                              self._crypto.decrypt_bytes(attachment.content_enc)))
        client = self._clients.get(item.bot)
        try:
            if client is None:
                raise TelegramUnavailable(f"{item.bot} botunun tokeni təyin olunmayıb")
            message_id = self._call(client, item)
        except RetryAfter as e:
            self._reschedule(outbox_id, seconds=e.seconds, error=str(e), count_attempt=False)
        except Forbidden as e:
            self._dead(outbox_id, error=f"Forbidden: {e}", reason="bot bloklanıb və ya söhbət başlanmayıb")
        except BadRequest as e:
            self._dead(outbox_id, error=f"BadRequest: {e}", reason="Telegram sorğunu qəbul etmədi")
        except TelegramError as e:
            self._retry_or_dead(outbox_id, error=f"{type(e).__name__}: {e}")
        except Exception as e:  # gözlənilməz xəta worker-i dayandırmamalıdır
            log.exception("outbox %s: gözlənilməz xəta", outbox_id)
            self._retry_or_dead(outbox_id, error=type(e).__name__)
        else:
            self._sent(outbox_id, message_id)

    @staticmethod
    def _call(client: Sender, item: _Item) -> int | None:
        markup = item.payload.get("reply_markup")
        if item.method == "send_message":
            return client.send_message(item.chat_id, item.payload["text"], reply_markup=markup)
        if item.method == "edit_message_text":
            client.edit_message_text(item.chat_id, item.payload["message_id"], item.payload["text"],
                                     reply_markup=markup)
            return None
        if item.method == "send_document":
            if item.document is None:  # əlavə silinib (saxlama müddəti) və ya açar yoxdur
                raise BadRequest("əlavə tapılmadı")
            name, content = item.document
            return client.send_document(item.chat_id, name, content, caption=item.payload.get("caption"))
        raise BadRequest(f"naməlum metod: {item.method}")

    # ---------- nəticələr ----------
    def _sent(self, outbox_id: int, message_id: int | None) -> None:
        with self._sessions() as s, s.begin():
            row = s.get(Outbox, outbox_id, with_for_update=True)
            row.status, row.sent_at, row.last_error = OutboxStatus.sent, self._clock(), None
            if row.purpose == SECRET_PURPOSE:
                row.payload = {"text": REDACTED}
            elif row.method == "send_message" and row.purpose and message_id is not None:
                s.add(TelegramMessage(bot=row.bot, chat_id=row.chat_id, message_id=message_id,
                                      appeal_id=row.appeal_id, purpose=row.purpose))

    def _reschedule(self, outbox_id: int, *, seconds: int, error: str, count_attempt: bool = True) -> None:
        with self._sessions() as s, s.begin():
            row = s.get(Outbox, outbox_id, with_for_update=True)
            row.status = OutboxStatus.pending
            row.next_attempt_at = self._clock() + timedelta(seconds=seconds)
            row.last_error = error[:1000]
            if not count_attempt:  # 429: Telegram-ın tələbi ilə gözləmə cəhd sayılmır
                row.attempts = max(row.attempts - 1, 0)

    def _retry_or_dead(self, outbox_id: int, *, error: str) -> None:
        delays = self._config.retry_delays_seconds
        with self._sessions() as s:
            attempts = s.get(Outbox, outbox_id).attempts
        if attempts > len(delays):
            self._dead(outbox_id, error=error, reason=f"{attempts} cəhddən sonra")
        else:
            self._reschedule(outbox_id, seconds=delays[attempts - 1], error=error)

    def _dead(self, outbox_id: int, *, error: str, reason: str) -> None:
        with self._sessions() as s, s.begin():
            row = s.get(Outbox, outbox_id, with_for_update=True)
            row.status, row.last_error = OutboxStatus.dead, error[:1000]
            if row.purpose == SECRET_PURPOSE:
                row.payload = {"text": REDACTED}
            # Kart redaktəsinin alınmaması informativdir. Xəbərdarlığın özü alınmasa, dövrə yaranmasın
            if row.method == "send_message" and row.purpose != ALERT_PURPOSE:
                self._alert(s, row, reason)
        log.warning("outbox %s göndərilmədi: %s", outbox_id, error)

    def _alert(self, session: Session, row: Outbox, reason: str) -> None:
        appeal = session.get(Appeal, row.appeal_id) if row.appeal_id else None
        rendered = cards.delivery_failed_alert(appeal.number if appeal else None, reason,
                                               to_citizen=row.bot == BotKind.citizen)
        for coordinator in recipients.coordinators(session):
            enqueue_rendered(session, bot=BotKind.staff, chat_id=coordinator.telegram_user_id, rendered=rendered,
                             appeal_id=row.appeal_id, purpose=ALERT_PURPOSE, now=self._clock())
