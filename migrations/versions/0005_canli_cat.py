"""canlı çat: vətəndaş ↔ operator (real vaxtda)

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-22 22:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = '0005'
down_revision: str | None = '0004'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'live_chats',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('citizen_telegram_id', sa.BigInteger(), nullable=False),
        sa.Column('staff_id', sa.Integer(), sa.ForeignKey('staff.id'), nullable=True),
        sa.Column('status', sa.String(16), server_default='waiting', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_activity_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('closed_by', sa.String(16), nullable=True),
    )
    op.create_index('ux_live_chats_open_citizen', 'live_chats', ['citizen_telegram_id'], unique=True,
                    postgresql_where=sa.text("status IN ('waiting', 'active')"))
    op.create_index('ux_live_chats_active_staff', 'live_chats', ['staff_id'], unique=True,
                    postgresql_where=sa.text("status = 'active'"))


def downgrade() -> None:
    op.drop_table('live_chats')
