"""əlavələr: vətəndaşın foto və sənədləri (şifrəli)

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-22 18:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = '0003'
down_revision: str | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'attachments',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('owner_telegram_id', sa.BigInteger(), nullable=False),
        sa.Column('appeal_id', sa.BigInteger(), sa.ForeignKey('appeals.id', ondelete='CASCADE'), nullable=True),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('mime', sa.String(100), nullable=False),
        sa.Column('size', sa.Integer(), nullable=False),
        sa.Column('name_enc', sa.Text(), nullable=False),
        sa.Column('content_enc', sa.LargeBinary(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_attachments_owner_telegram_id', 'attachments', ['owner_telegram_id'])
    op.create_index('ix_attachments_appeal_id', 'attachments', ['appeal_id'])


def downgrade() -> None:
    op.drop_table('attachments')
