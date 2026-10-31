"""fulltext refreshed at

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-30 23:09:33.427686
"""
from alembic import op
import sqlalchemy as sa


revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('paper_fulltext', schema=None) as batch_op:
        batch_op.add_column(sa.Column('refreshed_at', sa.DateTime(timezone=True), nullable=True))



def downgrade() -> None:
    with op.batch_alter_table('paper_fulltext', schema=None) as batch_op:
        batch_op.drop_column('refreshed_at')

