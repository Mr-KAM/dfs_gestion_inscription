"""coaches of a training cohort, with their role in it

Revision ID: 0034
Revises: 0033
Create Date: 2026-11-12 10:00:00

"""
import sqlalchemy as sa
from alembic import op

revision = '0034'
down_revision = '0033'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'cohort_coaches',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('cohort_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('role', sa.String(length=100), nullable=False),
        sa.ForeignKeyConstraint(['cohort_id'], ['cohorts.id'], name=op.f('fk_cohort_coaches_cohort_id_cohorts'),
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_cohort_coaches_user_id_users'),
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_cohort_coaches')),
        sa.UniqueConstraint('cohort_id', 'user_id', name='uq_cohort_coaches_cohort_user'),
    )
    op.create_index(op.f('ix_cohort_coaches_cohort_id'), 'cohort_coaches', ['cohort_id'], unique=False)
    op.create_index(op.f('ix_cohort_coaches_user_id'), 'cohort_coaches', ['user_id'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_cohort_coaches_user_id'), table_name='cohort_coaches')
    op.drop_index(op.f('ix_cohort_coaches_cohort_id'), table_name='cohort_coaches')
    op.drop_table('cohort_coaches')
