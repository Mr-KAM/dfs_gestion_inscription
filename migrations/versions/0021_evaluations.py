"""individual SAS evaluation of a student (grid filled by a coach or an admin)

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-03 10:00:00

"""
import sqlalchemy as sa
from alembic import op

revision = '0021'
down_revision = '0020'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'evaluations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('candidate_id', sa.Integer(), nullable=False),
        sa.Column('ratings', sa.JSON(), nullable=False),
        sa.Column('evidence', sa.JSON(), nullable=False),
        sa.Column('comment', sa.Text(), nullable=True),
        sa.Column('updated_by', sa.Integer(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'],
                                name=op.f('fk_evaluations_candidate_id_candidates'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['updated_by'], ['users.id'], name=op.f('fk_evaluations_updated_by_users'),
                                ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_evaluations')),
        sa.UniqueConstraint('candidate_id', name=op.f('uq_evaluations_candidate_id')),
    )


def downgrade():
    op.drop_table('evaluations')
