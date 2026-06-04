"""driver enrichment — адрес встречи и контекст

Revision ID: 0003_driver_enrichment
Revises: 0002_payment_void
Create Date: stage 1

Поля для обогащения водителя (контакты/контекст для сборщика):
  address — адрес встречи, context — закреплённая памятка.
(phone и in_park уже были в базовой схеме; комментарии — таблица notes.)
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0003_driver_enrichment'
down_revision: Union[str, Sequence[str], None] = '0002_payment_void'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('drivers') as b:
        b.add_column(sa.Column('address', sa.Text(), nullable=True))
        b.add_column(sa.Column('context', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('drivers') as b:
        b.drop_column('context')
        b.drop_column('address')
