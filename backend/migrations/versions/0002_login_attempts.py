"""login attempts: защита от подбора пароля

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-23 12:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0002'
down_revision: str | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('login_attempts',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('login', sa.String(length=64), nullable=False),
    sa.Column('success', sa.Boolean(), nullable=False),
    sa.Column('ip', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_login_attempts_login', 'login_attempts', ['login', 'created_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_login_attempts_login', table_name='login_attempts')
    op.drop_table('login_attempts')
