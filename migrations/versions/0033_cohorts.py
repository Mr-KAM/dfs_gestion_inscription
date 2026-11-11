"""training cohorts and their enrollments (DFS matricule, arrival, signature, bedroom, equipment)

Revision ID: 0033
Revises: 0032
Create Date: 2026-11-11 10:00:00

"""
import sqlalchemy as sa
from alembic import op

revision = '0033'
down_revision = '0032'
branch_labels = None
depends_on = None


def _fk(table, column, target, ondelete):
    return sa.ForeignKeyConstraint([column], [f'{target}.id'], name=op.f(f'fk_{table}_{column}_{target}'),
                                   ondelete=ondelete)


def upgrade():
    op.create_table(
        'cohorts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('description', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_cohorts')),
        sa.UniqueConstraint('name', name=op.f('uq_cohorts_name')),
    )
    op.create_table(
        'cohort_enrollments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('cohort_id', sa.Integer(), nullable=False),
        sa.Column('candidate_id', sa.Integer(), nullable=False),
        sa.Column('matricule', sa.String(length=12), nullable=True),
        sa.Column('arrival_date', sa.Date(), nullable=True),
        sa.Column('bedroom', sa.String(length=50), nullable=True),
        sa.Column('equipment', sa.String(length=255), nullable=True),
        sa.Column('signature', sa.LargeBinary(), nullable=True),
        sa.Column('signature_type', sa.String(length=20), nullable=True),
        sa.Column('validated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('validated_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        _fk('cohort_enrollments', 'cohort_id', 'cohorts', 'CASCADE'),
        _fk('cohort_enrollments', 'candidate_id', 'candidates', 'CASCADE'),
        _fk('cohort_enrollments', 'validated_by', 'users', 'SET NULL'),
        sa.CheckConstraint('(validated_at IS NULL) = (matricule IS NULL)',
                           name=op.f('ck_cohort_enrollments_validated_matricule')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_cohort_enrollments')),
        sa.UniqueConstraint('candidate_id', name=op.f('uq_cohort_enrollments_candidate_id')),
        sa.UniqueConstraint('matricule', name=op.f('uq_cohort_enrollments_matricule')),
    )
    op.create_index(op.f('ix_cohort_enrollments_cohort_id'), 'cohort_enrollments', ['cohort_id'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_cohort_enrollments_cohort_id'), table_name='cohort_enrollments')
    op.drop_table('cohort_enrollments')
    op.drop_table('cohorts')
