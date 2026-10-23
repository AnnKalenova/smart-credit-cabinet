"""одно согласие каждого вида на заявку; дата подписания договора; индекс попыток входа по адресу

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-23 14:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0003'
down_revision: str | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # дубли, если они успели появиться, схлопываются до самой ранней подписи
    op.execute(
        """
        DELETE FROM consents a
        USING consents b
        WHERE a.application_id = b.application_id
          AND a.consent_type = b.consent_type
          AND (a.signed_at, a.id::text) > (b.signed_at, b.id::text)
        """
    )
    op.create_unique_constraint('uq_consents_application_type', 'consents', ['application_id', 'consent_type'])
    op.add_column('offers', sa.Column('signed_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_login_attempts_ip', 'login_attempts', ['ip', 'created_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_login_attempts_ip', table_name='login_attempts')
    op.drop_column('offers', 'signed_at')
    op.drop_constraint('uq_consents_application_type', 'consents', type_='unique')
