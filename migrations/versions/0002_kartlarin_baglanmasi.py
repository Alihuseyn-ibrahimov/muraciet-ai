"""kartların bağlanması: telegram_messages.closed_at

Kartın düymələri silinəndə (aktivliyini itirəndə) qeyd olunur ki, kart bir daha yenilənməsin.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-21 14:01:40.167649
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = '0002'
down_revision: str | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('telegram_messages', sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('telegram_messages', 'closed_at')
