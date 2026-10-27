"""Saxlama müddəti təmizliyi (spec 9-cu bölmə, plan 8.5). n8n gündə bir dəfə `POST /internal/retention/cleanup` çağırır.

RETENTION_YEARS keçmiş və son vəziyyətdə olan (bağlanmış, aidiyyəti üzrə göndərilmiş, baxılmamış saxlanılmış)
müraciətlərdə şəxsi məlumat silinir, statistika qalır:
- müraciətin mövzusu, xülasəsi, təsviri və yazışmaların mətni → "[silinib]"; nömrə, kateqoriya, tarixlər qalır;
- artıq aktiv müraciəti olmayan vətəndaşın adı, telefonu, FİN-i, ünvanı və Telegram ID-si silinir;
- müraciətin foto və sənəd əlavələri silinir;
- bitmiş köhnə canlı çatlar (vətəndaşın Telegram ID-si ilə) silinir;
- köhnə chat_logs, göndərilmiş outbox mesajları (kartlarda telefon və ünvan var) və silinən vətəndaşın
  dialoq yaddaşı (LangGraph checkpoint-ləri) silinir.

Qalıq risk: appeal_events (audit) dəyişdirilə bilməz (tətbiq rolunun UPDATE/DELETE hüququ yoxdur). Onun
payload-unda əməkdaşın yazdığı səbəb və şərhlər qalır. RETENTION_YEARS təyin olunmayıbsa, heç nə silinmir;
yalnız müraciətə çevrilməmiş əlavələr (2 gündən köhnə) həmişə silinir.
"""
import logging
from datetime import datetime, timedelta

from sqlalchemy import Engine, delete, or_, select, text, update
from sqlalchemy.orm import Session

from app.db.tables import Appeal, AppealMessage, Attachment, ChatLog, Citizen, LiveChat, Outbox
from app.workflow.states import AppealStatus, OutboxStatus

log = logging.getLogger(__name__)
REDACTED = "[silinib]"
FINAL = (AppealStatus.closed, AppealStatus.redirected, AppealStatus.not_reviewed)
CHECKPOINT_TABLES = ("checkpoint_writes", "checkpoint_blobs", "checkpoints")
ORPHAN_ATTACHMENT_DAYS = 2  # müraciətə çevrilməmiş əlavə (vətəndaş fikrindən daşınıb, sessiya bitib)


def _cutoff(now: datetime, years: int) -> datetime:
    try:
        return now.replace(year=now.year - years)
    except ValueError:  # 29 fevral
        return now.replace(year=now.year - years, day=28)


def retention_cleanup(engine: Engine, *, now: datetime, years: int | None) -> dict:
    with Session(engine) as s, s.begin():
        orphans = s.execute(delete(Attachment).where(
            Attachment.appeal_id.is_(None),
            Attachment.created_at < now - timedelta(days=ORPHAN_ATTACHMENT_DAYS))).rowcount
    if not years:
        log.warning("RETENTION_YEARS təyin olunmayıb: saxlama müddəti təmizliyi işləmir (hüquqşünasın qərarı lazımdır)")
        return {"skipped": True, "orphan_attachments": orphans}
    cutoff = _cutoff(now, years)
    with Session(engine) as s, s.begin():
        old = select(Appeal.id).where(Appeal.status.in_(FINAL), Appeal.closed_at < cutoff,
                                      Appeal.summary != REDACTED)
        ids = list(s.scalars(old))
        if ids:
            s.execute(update(Appeal).where(Appeal.id.in_(ids))
                      .values(subject=REDACTED, summary=REDACTED, description=REDACTED, not_reviewed_reason=None,
                              extension_reason=None, ai_department_reason=None))
            s.execute(update(AppealMessage).where(AppealMessage.appeal_id.in_(ids)).values(text=REDACTED, meta=None))
            attachments = s.execute(delete(Attachment).where(Attachment.appeal_id.in_(ids))).rowcount
        else:
            attachments = 0

        active = select(Appeal.citizen_id).where(or_(Appeal.status.not_in(FINAL), Appeal.closed_at >= cutoff))
        chatted = select(ChatLog.citizen_telegram_id).where(ChatLog.created_at >= cutoff)
        citizens = list(s.scalars(select(Citizen).where(
            Citizen.updated_at < cutoff, Citizen.telegram_user_id > 0, Citizen.id.not_in(active),
            Citizen.telegram_user_id.not_in(chatted))))
        checkpoints = chat_logs = 0
        for citizen in citizens:
            thread = f"citizen:{citizen.telegram_user_id}"
            for table in CHECKPOINT_TABLES:
                checkpoints += s.execute(text(f"DELETE FROM {table} WHERE thread_id = :t"), {"t": thread}).rowcount
            chat_logs += s.execute(delete(ChatLog).where(
                ChatLog.citizen_telegram_id == citizen.telegram_user_id)).rowcount
            citizen.telegram_user_id = -citizen.id  # unikal qalır, şəxsi identifikator deyil
            for field in ("last_name", "first_name", "patronymic", "phone_enc", "phone_hash", "fin_enc", "fin_hash",
                          "address_enc"):
                setattr(citizen, field, None)

        chat_logs += s.execute(delete(ChatLog).where(ChatLog.created_at < cutoff)).rowcount
        live_chats = s.execute(delete(LiveChat).where(LiveChat.status.in_(["closed", "expired"]),
                                                      LiveChat.closed_at < cutoff)).rowcount
        outbox = s.execute(delete(Outbox).where(Outbox.created_at < cutoff,
                                                Outbox.status.in_([OutboxStatus.sent, OutboxStatus.dead]))).rowcount
    summary = {"appeals": len(ids), "citizens": len(citizens), "chat_logs": chat_logs, "outbox": outbox,
               "checkpoints": checkpoints, "attachments": attachments, "orphan_attachments": orphans,
               "live_chats": live_chats}
    log.info("saxlama müddəti təmizliyi: %s", summary)
    return summary
