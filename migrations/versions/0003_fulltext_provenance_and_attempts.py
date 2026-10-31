"""fulltext provenance and attempts

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-30 22:50:13.975188
"""
from alembic import op
import sqlalchemy as sa


revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('fulltext_attempts',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('paper_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('provider', sa.String(length=40), nullable=False),
    sa.Column('outcome', sa.String(length=20), nullable=False),
    sa.Column('detail', sa.Text(), nullable=True),
    sa.Column('attempted_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['paper_id'], ['papers.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('fulltext_attempts', schema=None) as batch_op:
        batch_op.create_index('ix_fta_paper_provider', ['paper_id', 'provider'], unique=False)

    with op.batch_alter_table('paper_fulltext', schema=None) as batch_op:
        batch_op.add_column(sa.Column('provider', sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column('tier', sa.String(length=10), nullable=True))
        batch_op.add_column(sa.Column('rights', sa.String(length=10), nullable=True))
        batch_op.add_column(sa.Column('licence_key', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('print_allowed', sa.Boolean(), server_default=sa.false(), nullable=False))
        batch_op.add_column(sa.Column('words', sa.Integer(), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('checked_at', sa.DateTime(timezone=True), nullable=True))


    # new tables need row level security on Postgres too (see 0001): no policies = Supabase's REST API sees nothing
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute('ALTER TABLE "fulltext_attempts" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    with op.batch_alter_table('paper_fulltext', schema=None) as batch_op:
        batch_op.drop_column('checked_at')
        batch_op.drop_column('words')
        batch_op.drop_column('print_allowed')
        batch_op.drop_column('licence_key')
        batch_op.drop_column('rights')
        batch_op.drop_column('tier')
        batch_op.drop_column('provider')

    with op.batch_alter_table('fulltext_attempts', schema=None) as batch_op:
        batch_op.drop_index('ix_fta_paper_provider')

    op.drop_table('fulltext_attempts')
