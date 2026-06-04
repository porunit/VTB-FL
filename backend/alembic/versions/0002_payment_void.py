"""payment void (аннулирование) — append-only журнал

Revision ID: 0002_payment_void
Revises: 57e97efbd295
Create Date: stage 1

Платёж не удаляется и не правится, а помечается аннулированным
(voided_at/voided_by/void_reason). В баланс входят только voided_at IS NULL.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0002_payment_void'
down_revision: Union[str, Sequence[str], None] = '57e97efbd295'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('payments') as b:
        b.add_column(sa.Column('voided_at', sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column('voided_by', sa.Integer(), nullable=True))
        b.add_column(sa.Column('void_reason', sa.Text(), nullable=True))
        b.create_foreign_key('fk_payment_voided_by_users', 'users', ['voided_by'], ['id'])


def downgrade() -> None:
    with op.batch_alter_table('payments') as b:
        b.drop_constraint('fk_payment_voided_by_users', type_='foreignkey')
        b.drop_column('void_reason')
        b.drop_column('voided_by')
        b.drop_column('voided_at')
