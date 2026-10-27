"""bilik bazası təklifləri: kurator agenti (admin təsdiqi ilə)

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-22 20:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = '0004'
down_revision: str | None = '0003'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'kb_entries',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('appeal_id', sa.BigInteger(), sa.ForeignKey('appeals.id', ondelete='SET NULL'), nullable=True,
                  unique=True),
        sa.Column('question', sa.Text(), nullable=False),
        sa.Column('answer', sa.Text(), nullable=False),
        sa.Column('status', sa.String(16), server_default='pending', nullable=False),
        sa.Column('decided_by_staff_id', sa.Integer(), sa.ForeignKey('staff.id'), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_kb_entries_status', 'kb_entries', ['status'])


def downgrade() -> None:
    op.drop_table('kb_entries')
