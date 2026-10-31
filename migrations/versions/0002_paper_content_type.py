"""paper content type

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-30 22:41:16.847392
"""
from alembic import op
import sqlalchemy as sa


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('papers', schema=None) as batch_op:
        batch_op.add_column(sa.Column('content_type', sa.String(length=24), server_default='journal_article', nullable=False))
        batch_op.create_index(batch_op.f('ix_papers_content_type'), ['content_type'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('papers', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_papers_content_type'))
        batch_op.drop_column('content_type')

