"""newsletter issues and published label

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-31 00:35:19.292454
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('issues',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('profile_id', sa.String(length=64), nullable=False),
    sa.Column('issue_key', sa.String(length=80), nullable=False),
    sa.Column('window_start', sa.DateTime(timezone=True), nullable=False),
    sa.Column('window_end', sa.DateTime(timezone=True), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('subject', sa.String(length=300), nullable=False),
    sa.Column('html', sa.Text(), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('stats', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('draft_ref', sa.String(length=300), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['profile_id'], ['profiles.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('profile_id', 'issue_key')
    )
    op.create_table('issue_items',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('issue_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('profile_id', sa.String(length=64), nullable=False),
    sa.Column('paper_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('role', sa.String(length=12), nullable=False),
    sa.Column('section', sa.String(length=40), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('snapshot', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.ForeignKeyConstraint(['issue_id'], ['issues.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['paper_id'], ['papers.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['profile_id'], ['profiles.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('profile_id', 'paper_id', 'role')
    )
    with op.batch_alter_table('papers', schema=None) as batch_op:
        batch_op.add_column(sa.Column('published_label', sa.String(length=30), nullable=True))


    op.execute("UPDATE papers SET published_label = CAST(published_date AS TEXT) WHERE published_date IS NOT NULL AND published_label IS NULL")
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in ("issues", "issue_items"):
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    with op.batch_alter_table('papers', schema=None) as batch_op:
        batch_op.drop_column('published_label')

    op.drop_table('issue_items')
    op.drop_table('issues')
