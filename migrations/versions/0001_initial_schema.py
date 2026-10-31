"""initial schema

Revision ID: 0001
Revises: 
Create Date: 2026-10-30 22:18:50.359027
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('fields',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('config', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('config_hash', sa.String(length=40), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('papers',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('doi', sa.String(length=300), nullable=True),
    sa.Column('pmid', sa.String(length=20), nullable=True),
    sa.Column('pmcid', sa.String(length=20), nullable=True),
    sa.Column('nct_id', sa.String(length=20), nullable=True),
    sa.Column('title_fp', sa.String(length=40), nullable=True),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('abstract', sa.Text(), nullable=False),
    sa.Column('journal', sa.String(length=400), nullable=True),
    sa.Column('issns', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('languages', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('pub_types', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('authors', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('published_date', sa.Date(), nullable=True),
    sa.Column('first_entry_date', sa.Date(), nullable=True),
    sa.Column('url', sa.Text(), nullable=True),
    sa.Column('is_retracted', sa.Boolean(), nullable=False),
    sa.Column('retraction_sources', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('is_retraction_notice', sa.Boolean(), nullable=False),
    sa.Column('retraction_target_doi', sa.String(length=300), nullable=True),
    sa.Column('retraction_target_pmid', sa.String(length=20), nullable=True),
    sa.Column('retraction_target_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('oa_status', sa.String(length=20), nullable=True),
    sa.Column('licences', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['retraction_target_id'], ['papers.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('doi'),
    sa.UniqueConstraint('nct_id'),
    sa.UniqueConstraint('pmcid'),
    sa.UniqueConstraint('pmid')
    )
    with op.batch_alter_table('papers', schema=None) as batch_op:
        batch_op.create_index('ix_papers_entry', ['first_entry_date'], unique=False)
        batch_op.create_index('ix_papers_first_seen', ['first_seen_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_papers_title_fp'), ['title_fp'], unique=False)

    op.create_table('search_specs',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('spec_key', sa.String(length=40), nullable=False),
    sa.Column('path', sa.String(length=20), nullable=False),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('label', sa.String(length=300), nullable=False),
    sa.Column('params', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('meta', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('gated', sa.Boolean(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('spec_key')
    )
    op.create_table('bridge_terms',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('field_id', sa.String(length=64), nullable=False),
    sa.Column('term', sa.String(length=200), nullable=False),
    sa.Column('approved', sa.Boolean(), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['field_id'], ['fields.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('field_id', 'term')
    )
    op.create_table('journals',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('field_id', sa.String(length=64), nullable=False),
    sa.Column('key', sa.String(length=100), nullable=False),
    sa.Column('name', sa.String(length=300), nullable=False),
    sa.Column('track', sa.String(length=2), nullable=False),
    sa.Column('tier', sa.String(length=20), nullable=False),
    sa.Column('issns', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('aliases', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.ForeignKeyConstraint(['field_id'], ['fields.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('field_id', 'key')
    )
    op.create_table('paper_fulltext',
    sa.Column('paper_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('format', sa.String(length=20), nullable=True),
    sa.Column('licence', sa.String(length=300), nullable=True),
    sa.Column('source_url', sa.String(length=1000), nullable=True),
    sa.Column('text', sa.Text(), nullable=True),
    sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['paper_id'], ['papers.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('paper_id')
    )
    op.create_table('paper_links',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('paper_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('url', sa.String(length=1000), nullable=False),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('is_free', sa.Boolean(), nullable=True),
    sa.Column('licence', sa.String(length=300), nullable=True),
    sa.ForeignKeyConstraint(['paper_id'], ['papers.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('paper_id', 'url')
    )
    op.create_table('profiles',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('field_id', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('config', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['field_id'], ['fields.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('researchers',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('field_id', sa.String(length=64), nullable=False),
    sa.Column('key', sa.String(length=100), nullable=False),
    sa.Column('name', sa.String(length=300), nullable=False),
    sa.Column('track', sa.String(length=2), nullable=False),
    sa.Column('pubmed_names', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('pubmed_name_ambiguous', sa.Boolean(), nullable=False),
    sa.Column('openalex_ids', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('orcid', sa.String(length=25), nullable=True),
    sa.Column('aliases', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.ForeignKeyConstraint(['field_id'], ['fields.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('field_id', 'key')
    )
    op.create_table('retraction_checks',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('paper_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('result', sa.String(length=10), nullable=False),
    sa.Column('detail', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('checked_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['paper_id'], ['papers.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('subjects',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('field_id', sa.String(length=64), nullable=False),
    sa.Column('code', sa.String(length=20), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('track', sa.String(length=2), nullable=False),
    sa.Column('terms', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('generic_terms', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.ForeignKeyConstraint(['field_id'], ['fields.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('field_id', 'code')
    )
    op.create_table('profile_journals',
    sa.Column('profile_id', sa.String(length=64), nullable=False),
    sa.Column('journal_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.ForeignKeyConstraint(['journal_id'], ['journals.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['profile_id'], ['profiles.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('profile_id', 'journal_id')
    )
    op.create_table('profile_matches',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('profile_id', sa.String(length=64), nullable=False),
    sa.Column('paper_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('gate_status', sa.String(length=10), nullable=False),
    sa.Column('gate_reason', sa.String(length=200), nullable=False),
    sa.Column('language_status', sa.String(length=10), nullable=False),
    sa.Column('track', sa.String(length=2), nullable=False),
    sa.Column('exploratory', sa.Boolean(), nullable=False),
    sa.Column('subjects', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('entities', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('first_matched_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['paper_id'], ['papers.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['profile_id'], ['profiles.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('profile_id', 'paper_id')
    )
    with op.batch_alter_table('profile_matches', schema=None) as batch_op:
        batch_op.create_index('ix_pm_profile_status', ['profile_id', 'gate_status'], unique=False)

    op.create_table('profile_researchers',
    sa.Column('profile_id', sa.String(length=64), nullable=False),
    sa.Column('researcher_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.ForeignKeyConstraint(['profile_id'], ['profiles.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['researcher_id'], ['researchers.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('profile_id', 'researcher_id')
    )
    op.create_table('profile_search_specs',
    sa.Column('profile_id', sa.String(length=64), nullable=False),
    sa.Column('spec_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.ForeignKeyConstraint(['profile_id'], ['profiles.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['spec_id'], ['search_specs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('profile_id', 'spec_id')
    )
    op.create_table('profile_subjects',
    sa.Column('profile_id', sa.String(length=64), nullable=False),
    sa.Column('subject_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.ForeignKeyConstraint(['profile_id'], ['profiles.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['subject_id'], ['subjects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('profile_id', 'subject_id')
    )
    op.create_table('runs',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('profile_id', sa.String(length=64), nullable=True),
    sa.Column('trigger', sa.String(length=20), nullable=False),
    sa.Column('window_start', sa.Date(), nullable=False),
    sa.Column('window_end', sa.Date(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('stats', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.ForeignKeyConstraint(['profile_id'], ['profiles.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('paper_hits',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('paper_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('run_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('spec_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('path', sa.String(length=20), nullable=False),
    sa.Column('label', sa.String(length=300), nullable=False),
    sa.Column('gated', sa.Boolean(), nullable=False),
    sa.Column('confidence', sa.String(length=20), nullable=False),
    sa.Column('source_record_id', sa.String(length=120), nullable=False),
    sa.Column('source_entry_date', sa.Date(), nullable=True),
    sa.Column('source_published_date', sa.Date(), nullable=True),
    sa.Column('found_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['paper_id'], ['papers.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['spec_id'], ['search_specs.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('paper_id', 'spec_id', 'run_id', 'source_record_id')
    )
    op.create_table('source_run_log',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('run_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('spec_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('path', sa.String(length=20), nullable=False),
    sa.Column('label', sa.String(length=300), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('n_records', sa.Integer(), nullable=False),
    sa.Column('n_new_papers', sa.Integer(), nullable=False),
    sa.Column('total_reported', sa.Integer(), nullable=True),
    sa.Column('requests', sa.Integer(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('duration_ms', sa.Integer(), nullable=False),
    sa.Column('logged_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['spec_id'], ['search_specs.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('source_run_log', schema=None) as batch_op:
        batch_op.create_index('ix_srl_spec_run', ['spec_id', 'run_id'], unique=False)


    # Supabase exposes the public schema through its REST API. Row level security with no policies denies that API every
    # row; the backend connects as the database owner, which bypasses RLS. (Applied on Postgres only.)
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in sa.inspect(bind).get_table_names():
            if table != "alembic_version":
                op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    with op.batch_alter_table('source_run_log', schema=None) as batch_op:
        batch_op.drop_index('ix_srl_spec_run')

    op.drop_table('source_run_log')
    op.drop_table('paper_hits')
    op.drop_table('runs')
    op.drop_table('profile_subjects')
    op.drop_table('profile_search_specs')
    op.drop_table('profile_researchers')
    with op.batch_alter_table('profile_matches', schema=None) as batch_op:
        batch_op.drop_index('ix_pm_profile_status')

    op.drop_table('profile_matches')
    op.drop_table('profile_journals')
    op.drop_table('subjects')
    op.drop_table('retraction_checks')
    op.drop_table('researchers')
    op.drop_table('profiles')
    op.drop_table('paper_links')
    op.drop_table('paper_fulltext')
    op.drop_table('journals')
    op.drop_table('bridge_terms')
    op.drop_table('search_specs')
    with op.batch_alter_table('papers', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_papers_title_fp'))
        batch_op.drop_index('ix_papers_first_seen')
        batch_op.drop_index('ix_papers_entry')

    op.drop_table('papers')
    op.drop_table('fields')
