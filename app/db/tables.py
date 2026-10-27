"""Yeni sistemin cədvəlləri (spec 8-ci bölmə), muraciet_v2 bazası.

Köhnə prototipin modelləri (models.py) ayrıca metadata-dadır və Faza 9-da silinəcək.
Bütün vaxtlar vaxt zonası ilə (timestamptz) saxlanılır. FİN, telefon və ünvan yalnız şifrələnmiş
(_enc) və kor indeks (_hash) şəklindədir (app/privacy/crypto.py).
"""
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.workflow.states import (
    ActorType,
    AppealStatus,
    BotKind,
    Category,
    JobStatus,
    MessageKind,
    Outcome,
    OutboxStatus,
    Priority,
    StaffRole,
)


def _enum(enum_cls: type, name: str) -> Enum:
    return Enum(enum_cls, name=name, values_callable=lambda e: [m.value for m in e])


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB}


class Department(Base):
    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    competence: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))


class Staff(Base):
    __tablename__ = "staff"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    full_name: Mapped[str] = mapped_column(String(128))
    role: Mapped[StaffRole] = mapped_column(_enum(StaffRole, "staff_role"))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    last_assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()


class StaffInvite(Base):
    __tablename__ = "staff_invites"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)  # tokenin özü saxlanılmır
    role: Mapped[StaffRole] = mapped_column(_enum(StaffRole, "staff_role"))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"))
    created_by_staff_id: Mapped[int | None] = mapped_column(ForeignKey("staff.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    used_by_staff_id: Mapped[int | None] = mapped_column(ForeignKey("staff.id"))
    created_at: Mapped[datetime] = _created_at()


class Citizen(Base):
    __tablename__ = "citizens"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    last_name: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(64))
    patronymic: Mapped[str | None] = mapped_column(String(64))
    phone_enc: Mapped[str | None] = mapped_column(Text)
    phone_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    fin_enc: Mapped[str | None] = mapped_column(Text)
    fin_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    address_enc: Mapped[str | None] = mapped_column(Text)
    consent_version: Mapped[int | None] = mapped_column(Integer)
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Appeal(Base):
    __tablename__ = "appeals"
    __table_args__ = (Index("ix_appeals_assignee_status", "assigned_staff_id", "status"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str | None] = mapped_column(String(20), unique=True)  # MR-YYYY-NNNNNN
    citizen_id: Mapped[int] = mapped_column(ForeignKey("citizens.id"), index=True)
    category: Mapped[Category | None] = mapped_column(_enum(Category, "appeal_category"))
    priority: Mapped[Priority] = mapped_column(
        _enum(Priority, "appeal_priority"), default=Priority.normal, server_default=Priority.normal.value
    )
    subject: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[AppealStatus] = mapped_column(
        _enum(AppealStatus, "appeal_status"),
        default=AppealStatus.new,
        server_default=AppealStatus.new.value,
        index=True,
    )
    outcome: Mapped[Outcome | None] = mapped_column(_enum(Outcome, "appeal_outcome"))
    legal_issue: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    needs_approval: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    triage_failed: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"))
    assigned_staff_id: Mapped[int | None] = mapped_column(ForeignKey("staff.id"))
    ai_department_reason: Mapped[str | None] = mapped_column(Text)
    due_date: Mapped[date] = mapped_column(Date)
    original_due_date: Mapped[date] = mapped_column(Date)
    urgent_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    extension_reason: Mapped[str | None] = mapped_column(Text)
    redirect_target: Mapped[str | None] = mapped_column(String(255))
    redirect_letter_ref: Mapped[str | None] = mapped_column(String(64))  # m. 14.0.6
    not_reviewed_reason: Mapped[str | None] = mapped_column(Text)
    reopen_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    satisfaction: Mapped[int | None] = mapped_column(SmallInteger)  # 1 = 👍, -1 = 👎
    first_assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assign_work_hours: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    answer_work_hours: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AppealMessage(Base):
    __tablename__ = "appeal_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    appeal_id: Mapped[int] = mapped_column(ForeignKey("appeals.id"), index=True)
    kind: Mapped[MessageKind] = mapped_column(_enum(MessageKind, "message_kind"))
    author_staff_id: Mapped[int | None] = mapped_column(ForeignKey("staff.id"))
    text: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created_at()


class AppealEvent(Base):
    """Audit jurnalı. Tətbiqin DB rolunun bu cədvəldə UPDATE və DELETE hüququ yoxdur."""

    __tablename__ = "appeal_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    appeal_id: Mapped[int | None] = mapped_column(ForeignKey("appeals.id"), index=True)
    actor_type: Mapped[ActorType] = mapped_column(_enum(ActorType, "actor_type"))
    actor_id: Mapped[int | None] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(48))
    from_status: Mapped[AppealStatus | None] = mapped_column(_enum(AppealStatus, "appeal_status"))
    to_status: Mapped[AppealStatus | None] = mapped_column(_enum(AppealStatus, "appeal_status"))
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class TelegramMessage(Base):
    __tablename__ = "telegram_messages"
    __table_args__ = (UniqueConstraint("bot", "chat_id", "message_id", name="uq_telegram_messages_bot_chat_msg"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    bot: Mapped[BotKind] = mapped_column(_enum(BotKind, "bot_kind"))
    chat_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(BigInteger)
    appeal_id: Mapped[int | None] = mapped_column(ForeignKey("appeals.id"), index=True)
    purpose: Mapped[str] = mapped_column(String(32))
    # Kartın düymələri silinib (aktivliyini itirib): bir daha yenilənmir
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()


class Outbox(Base):
    __tablename__ = "outbox"
    __table_args__ = (Index("ix_outbox_status_next", "status", "next_attempt_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    bot: Mapped[BotKind] = mapped_column(_enum(BotKind, "bot_kind"))
    chat_id: Mapped[int] = mapped_column(BigInteger)
    method: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[OutboxStatus] = mapped_column(
        _enum(OutboxStatus, "outbox_status"), default=OutboxStatus.pending, server_default=OutboxStatus.pending.value
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_error: Mapped[str | None] = mapped_column(Text)
    appeal_id: Mapped[int | None] = mapped_column(ForeignKey("appeals.id"))
    purpose: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = _created_at()
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Job(Base):
    """Agent işlərinin növbəsi (triaj, cavab layihəsi): server restart olsa da itmir."""

    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_status_next", "status", "next_attempt_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    appeal_id: Mapped[int | None] = mapped_column(ForeignKey("appeals.id"))
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[JobStatus] = mapped_column(
        _enum(JobStatus, "job_status"), default=JobStatus.pending, server_default=JobStatus.pending.value
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChatLog(Base):
    """Vətəndaş botu ilə dialoq (yerli serverdə, saxlama müddəti qaydasına tabedir)."""

    __tablename__ = "chat_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    citizen_telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    intent: Mapped[str | None] = mapped_column(String(32))
    resolved_by_ai: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class Attachment(Base):
    """Vətəndaşın müraciətə əlavə etdiyi foto/sənəd. Məzmun və fayl adı Fernet ilə şifrəlidir.

    Müraciət yaranana qədər appeal_id boşdur (vətəndaş hələ yazır), sahibi Telegram ID ilə tanınır.
    """

    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    owner_telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    appeal_id: Mapped[int | None] = mapped_column(ForeignKey("appeals.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # photo | document
    mime: Mapped[str] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(Integer)
    name_enc: Mapped[str] = mapped_column(Text)
    content_enc: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = _created_at()


class KbEntry(Base):
    """Kurator agentinin bilik bazası təklifi. Bazaya yalnız admin təsdiqindən sonra düşür (status=approved).

    Mətn ümumiləşdirilib və şəxsi məlumatsızdır (agent yoxlayır), ona görə müraciət anonimləşdiriləndə də qalır.
    """

    __tablename__ = "kb_entries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    appeal_id: Mapped[int | None] = mapped_column(ForeignKey("appeals.id", ondelete="SET NULL"), unique=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending", index=True)
    decided_by_staff_id: Mapped[int | None] = mapped_column(ForeignKey("staff.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()


class LiveChat(Base):
    """Real vaxtda operator çatı: vətəndaş botu ↔ əməkdaş botu. Mesajların özü chat_logs-dadır.

    Vəziyyət: waiting (koordinatorlar gözləyir) → active (operator qəbul edib) → closed / expired.
    Bir vətəndaşın və bir operatorun eyni anda yalnız bir açıq çatı ola bilər (qismən unikal indekslər).
    """

    __tablename__ = "live_chats"
    __table_args__ = (
        Index("ux_live_chats_open_citizen", "citizen_telegram_id", unique=True,
              postgresql_where=text("status IN ('waiting', 'active')")),
        Index("ux_live_chats_active_staff", "staff_id", unique=True, postgresql_where=text("status = 'active'")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    citizen_telegram_id: Mapped[int] = mapped_column(BigInteger)
    staff_id: Mapped[int | None] = mapped_column(ForeignKey("staff.id"))
    status: Mapped[str] = mapped_column(String(16), default="waiting", server_default="waiting")
    created_at: Mapped[datetime] = _created_at()
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by: Mapped[str | None] = mapped_column(String(16))  # citizen | staff | timeout
