"""ilkin sxem: yeni sistemin cədvəlləri, enum tipləri, vector extension və v_daily_stats

Revision ID: 0001
Revises:
Create Date: 2026-10-21 12:45:55.919969

Avtomatik yaradılıb, sonra əl ilə düzəldilib:
- Enum tipləri bir neçə cədvəldə istifadə olunur (appeal_status, staff_role, bot_kind), ona görə əvvəlcədən
  bir dəfə yaradılır, cədvəllərdə isə create_type=False ilə istinad edilir.
- Enum dəyərləri app/workflow/states.py-dan import edilmir, buraya yazılır: migrasiya dondurulmuş
  tarixçədir, gələcək kod dəyişikliyi köhnə migrasiyanı səssizcə dəyişməməlidir.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENUMS: dict[str, tuple[str, ...]] = {
    "staff_role": ("coordinator", "staff", "head"),
    "appeal_category": ("complaint", "request", "suggestion", "information"),
    "appeal_priority": ("normal", "high", "urgent"),
    "appeal_status": (
        "new", "assigned", "waiting_citizen", "pending_approval", "answered", "closed", "redirected", "not_reviewed",
    ),
    "appeal_outcome": ("satisfied", "partial", "rejected", "explained"),
    "actor_type": ("citizen", "staff", "system", "ai"),
    "message_kind": (
        "staff_question", "citizen_reply", "ai_draft", "qa_report", "final_answer", "coordinator_comment",
    ),
    "job_status": ("pending", "running", "done", "dead"),
    "outbox_status": ("pending", "sending", "sent", "dead"),
    "bot_kind": ("citizen", "staff"),
}

# Rəhbərin gündəlik hesabatı üçün (n8n yalnız bunu oxuya bilir): yalnız aqreqat rəqəmlər, şəxsi məlumat yoxdur.
DAILY_STATS_VIEW = """
CREATE VIEW v_daily_stats AS
WITH today AS (SELECT (now() AT TIME ZONE 'Asia/Baku')::date AS d)
SELECT
    today.d AS day,
    (SELECT count(*) FROM appeals a
      WHERE (a.created_at AT TIME ZONE 'Asia/Baku')::date = today.d) AS appeals_created,
    (SELECT count(*) FROM appeals a
      WHERE (a.answered_at AT TIME ZONE 'Asia/Baku')::date = today.d) AS appeals_answered,
    (SELECT count(*) FROM appeals a
      WHERE (a.closed_at AT TIME ZONE 'Asia/Baku')::date = today.d) AS appeals_closed,
    (SELECT count(*) FROM appeals a
      WHERE a.status IN ('new', 'assigned', 'waiting_citizen', 'pending_approval')) AS open_total,
    (SELECT count(*) FROM appeals a WHERE a.status = 'new') AS unassigned_total,
    (SELECT count(*) FROM appeals a
      WHERE a.status IN ('new', 'assigned', 'waiting_citizen', 'pending_approval')
        AND a.due_date < today.d) AS overdue_total,
    (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY a.assign_work_hours) FROM appeals a
      WHERE (a.first_assigned_at AT TIME ZONE 'Asia/Baku')::date = today.d) AS median_assign_work_hours,
    (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY a.answer_work_hours) FROM appeals a
      WHERE (a.answered_at AT TIME ZONE 'Asia/Baku')::date = today.d) AS median_answer_work_hours,
    (SELECT count(*) FROM chat_logs c
      WHERE c.resolved_by_ai AND (c.created_at AT TIME ZONE 'Asia/Baku')::date = today.d) AS ai_answers,
    (SELECT round(avg(CASE WHEN a.satisfaction = 1 THEN 1.0 ELSE 0.0 END), 3) FROM appeals a
      WHERE a.satisfaction IS NOT NULL
        AND (a.answered_at AT TIME ZONE 'Asia/Baku')::date > today.d - 30) AS satisfaction_rate_30d
FROM today
"""


def enum(name: str) -> postgresql.ENUM:
    return postgresql.ENUM(*ENUMS[name], name=name, create_type=False)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")  # bilik bazası (langchain_postgres cədvəlləri)
    bind = op.get_bind()
    for name, values in ENUMS.items():
        postgresql.ENUM(*values, name=name).create(bind, checkfirst=True)

    op.create_table(
        "chat_logs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("citizen_telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("intent", sa.String(length=32), nullable=True),
        sa.Column("resolved_by_ai", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_chat_logs_citizen_telegram_id"), "chat_logs", ["citizen_telegram_id"])
    op.create_index(op.f("ix_chat_logs_created_at"), "chat_logs", ["created_at"])

    op.create_table(
        "citizens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("last_name", sa.String(length=64), nullable=True),
        sa.Column("first_name", sa.String(length=64), nullable=True),
        sa.Column("patronymic", sa.String(length=64), nullable=True),
        sa.Column("phone_enc", sa.Text(), nullable=True),
        sa.Column("phone_hash", sa.String(length=64), nullable=True),
        sa.Column("fin_enc", sa.Text(), nullable=True),
        sa.Column("fin_hash", sa.String(length=64), nullable=True),
        sa.Column("address_enc", sa.Text(), nullable=True),
        sa.Column("consent_version", sa.Integer(), nullable=True),
        sa.Column("consent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("telegram_user_id"),
    )
    op.create_index(op.f("ix_citizens_fin_hash"), "citizens", ["fin_hash"])
    op.create_index(op.f("ix_citizens_phone_hash"), "citizens", ["phone_hash"])

    op.create_table(
        "departments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("competence", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )

    op.create_table(
        "staff",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("full_name", sa.String(length=128), nullable=False),
        sa.Column("role", enum("staff_role"), nullable=False),
        sa.Column("is_admin", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("department_id", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("last_assigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["department_id"], ["departments.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("telegram_user_id"),
    )

    op.create_table(
        "appeals",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("number", sa.String(length=20), nullable=True),
        sa.Column("citizen_id", sa.Integer(), nullable=False),
        sa.Column("category", enum("appeal_category"), nullable=True),
        sa.Column("priority", enum("appeal_priority"), server_default="normal", nullable=False),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", enum("appeal_status"), server_default="new", nullable=False),
        sa.Column("outcome", enum("appeal_outcome"), nullable=True),
        sa.Column("legal_issue", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("needs_approval", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("triage_failed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("department_id", sa.Integer(), nullable=True),
        sa.Column("assigned_staff_id", sa.Integer(), nullable=True),
        sa.Column("ai_department_reason", sa.Text(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("original_due_date", sa.Date(), nullable=False),
        sa.Column("urgent_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("extension_reason", sa.Text(), nullable=True),
        sa.Column("redirect_target", sa.String(length=255), nullable=True),
        sa.Column("redirect_letter_ref", sa.String(length=64), nullable=True),
        sa.Column("not_reviewed_reason", sa.Text(), nullable=True),
        sa.Column("reopen_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("satisfaction", sa.SmallInteger(), nullable=True),
        sa.Column("first_assigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("assign_work_hours", sa.Numeric(precision=8, scale=2), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("answer_work_hours", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["assigned_staff_id"], ["staff.id"]),
        sa.ForeignKeyConstraint(["citizen_id"], ["citizens.id"]),
        sa.ForeignKeyConstraint(["department_id"], ["departments.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("number"),
    )
    op.create_index("ix_appeals_assignee_status", "appeals", ["assigned_staff_id", "status"])
    op.create_index(op.f("ix_appeals_citizen_id"), "appeals", ["citizen_id"])
    op.create_index(op.f("ix_appeals_status"), "appeals", ["status"])

    op.create_table(
        "staff_invites",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("role", enum("staff_role"), nullable=False),
        sa.Column("is_admin", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("department_id", sa.Integer(), nullable=True),
        sa.Column("created_by_staff_id", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("used_by_staff_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["created_by_staff_id"], ["staff.id"]),
        sa.ForeignKeyConstraint(["department_id"], ["departments.id"]),
        sa.ForeignKeyConstraint(["used_by_staff_id"], ["staff.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )

    op.create_table(
        "appeal_events",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("appeal_id", sa.BigInteger(), nullable=True),
        sa.Column("actor_type", enum("actor_type"), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), nullable=True),
        sa.Column("action", sa.String(length=48), nullable=False),
        sa.Column("from_status", enum("appeal_status"), nullable=True),
        sa.Column("to_status", enum("appeal_status"), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["appeal_id"], ["appeals.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_appeal_events_appeal_id"), "appeal_events", ["appeal_id"])
    op.create_index(op.f("ix_appeal_events_created_at"), "appeal_events", ["created_at"])

    op.create_table(
        "appeal_messages",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("appeal_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", enum("message_kind"), nullable=False),
        sa.Column("author_staff_id", sa.Integer(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["appeal_id"], ["appeals.id"]),
        sa.ForeignKeyConstraint(["author_staff_id"], ["staff.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_appeal_messages_appeal_id"), "appeal_messages", ["appeal_id"])

    op.create_table(
        "jobs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("appeal_id", sa.BigInteger(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", enum("job_status"), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["appeal_id"], ["appeals.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_jobs_status_next", "jobs", ["status", "next_attempt_at"])

    op.create_table(
        "outbox",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("bot", enum("bot_kind"), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("method", sa.String(length=32), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", enum("outbox_status"), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("appeal_id", sa.BigInteger(), nullable=True),
        sa.Column("purpose", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["appeal_id"], ["appeals.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_outbox_status_next", "outbox", ["status", "next_attempt_at"])

    op.create_table(
        "telegram_messages",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("bot", enum("bot_kind"), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("appeal_id", sa.BigInteger(), nullable=True),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["appeal_id"], ["appeals.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bot", "chat_id", "message_id", name="uq_telegram_messages_bot_chat_msg"),
    )
    op.create_index(op.f("ix_telegram_messages_appeal_id"), "telegram_messages", ["appeal_id"])

    op.execute(DAILY_STATS_VIEW)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_daily_stats")
    for table in [
        "telegram_messages", "outbox", "jobs", "appeal_messages", "appeal_events",
        "staff_invites", "appeals", "staff", "departments", "citizens", "chat_logs",
    ]:
        op.drop_table(table)  # indekslər cədvəllə birlikdə silinir
    bind = op.get_bind()
    for name in reversed(list(ENUMS)):
        postgresql.ENUM(name=name).drop(bind, checkfirst=True)
