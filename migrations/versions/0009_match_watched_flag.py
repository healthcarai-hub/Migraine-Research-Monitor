"""match watched flag

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-31 04:40:33.879630
"""
from alembic import op
import sqlalchemy as sa


revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('profile_matches', schema=None) as batch_op:
        batch_op.add_column(sa.Column('watched', sa.Boolean(), server_default=sa.false(), nullable=False))


    # backfill: a match is "watched" when it came from a watched journal or a researcher whose identity is not ambiguous
    import json
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, entities FROM profile_matches")).fetchall()
    for rid, ents in rows:
        data = ents if isinstance(ents, list) else json.loads(ents or "[]")
        if any(e.get("confidence") != "name_ambiguous" for e in data):
            bind.execute(sa.text("UPDATE profile_matches SET watched = :w WHERE id = :i").bindparams(sa.bindparam("w", True, type_=sa.Boolean()), i=rid))


def downgrade() -> None:
    with op.batch_alter_table('profile_matches', schema=None) as batch_op:
        batch_op.drop_column('watched')

