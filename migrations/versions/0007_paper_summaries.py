"""paper summaries

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-31 00:49:41.847412
"""
from alembic import op
import sqlalchemy as sa


revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('paper_summaries',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('paper_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('model', sa.String(length=120), nullable=False),
    sa.Column('prompt_version', sa.String(length=20), nullable=False),
    sa.Column('abstract_hash', sa.String(length=40), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['paper_id'], ['papers.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('paper_id', 'model', 'prompt_version', 'abstract_hash')
    )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute('ALTER TABLE "paper_summaries" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    op.drop_table('paper_summaries')
