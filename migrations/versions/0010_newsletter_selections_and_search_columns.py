"""newsletter selections and search columns

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-31 09:10:00.000000
"""
import json

from alembic import op
import sqlalchemy as sa


revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('newsletter_selections',
    sa.Column('profile_id', sa.String(length=64), nullable=False),
    sa.Column('kind', sa.String(length=12), nullable=False),
    sa.Column('key', sa.String(length=100), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['profile_id'], ['profiles.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('profile_id', 'kind', 'key')
    )
    with op.batch_alter_table('papers', schema=None) as batch_op:
        batch_op.add_column(sa.Column('author_names', sa.Text(), server_default='', nullable=False))
    with op.batch_alter_table('profile_matches', schema=None) as batch_op:
        batch_op.add_column(sa.Column('entity_tags', sa.Text(), server_default='', nullable=False))

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute('ALTER TABLE "newsletter_selections" ENABLE ROW LEVEL SECURITY')

    # backfill the two derived search strings for what is already stored
    from research_agent.domain.names import author_names, entity_tags
    for pid, authors in bind.execute(sa.text("SELECT id, authors FROM papers")).fetchall():
        data = authors if isinstance(authors, list) else json.loads(authors or "[]")
        bind.execute(sa.text("UPDATE papers SET author_names = :v WHERE id = :i"), {"v": author_names(data), "i": pid})
    for mid, ents in bind.execute(sa.text("SELECT id, entities FROM profile_matches")).fetchall():
        data = ents if isinstance(ents, list) else json.loads(ents or "[]")
        bind.execute(sa.text("UPDATE profile_matches SET entity_tags = :v WHERE id = :i"), {"v": entity_tags(data), "i": mid})


def downgrade() -> None:
    with op.batch_alter_table('profile_matches', schema=None) as batch_op:
        batch_op.drop_column('entity_tags')
    with op.batch_alter_table('papers', schema=None) as batch_op:
        batch_op.drop_column('author_names')
    op.drop_table('newsletter_selections')
