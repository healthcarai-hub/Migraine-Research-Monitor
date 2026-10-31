"""abstract source

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-31 04:06:20.896582
"""
from alembic import op
import sqlalchemy as sa


revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('papers', schema=None) as batch_op:
        batch_op.add_column(sa.Column('abstract_source', sa.String(length=20), server_default='source', nullable=False))



def downgrade() -> None:
    with op.batch_alter_table('papers', schema=None) as batch_op:
        batch_op.drop_column('abstract_source')

