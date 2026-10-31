"""pmcid found at

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-31 00:00:14.575620
"""
from alembic import op
import sqlalchemy as sa


revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('papers', schema=None) as batch_op:
        batch_op.add_column(sa.Column('pmcid_found_at', sa.DateTime(timezone=True), nullable=True))


    # Rows stored before this column existed: their PMC ID was learned during the run that stored them (collection looks it up straight away),
    # so first_seen_at is the best available value. Those rows therefore always count as "same day" in the lag report.
    op.execute("UPDATE papers SET pmcid_found_at = first_seen_at WHERE pmcid IS NOT NULL AND pmcid_found_at IS NULL")


def downgrade() -> None:
    with op.batch_alter_table('papers', schema=None) as batch_op:
        batch_op.drop_column('pmcid_found_at')

