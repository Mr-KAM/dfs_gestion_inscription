"""Zeus notifications: to a training cohort, one of its classes or one student

Revision ID: 0036
Revises: 0035
Create Date: 2026-11-12 18:00:00

"""
import sqlalchemy as sa
from alembic import op

revision = '0036'
down_revision = '0035'
branch_labels = None
depends_on = None


def _fk(column, target, ondelete):
    return sa.ForeignKeyConstraint([column], [f'{target}.id'], name=op.f(f'fk_zeus_notifications_{column}_{target}'),
                                   ondelete=ondelete)


def upgrade():
    op.create_table(
        'zeus_notifications',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('cohort_id', sa.Integer(), nullable=False),
        sa.Column('class_id', sa.Integer(), nullable=True),
        sa.Column('enrollment_id', sa.Integer(), nullable=True),
        sa.Column('title', sa.String(length=150), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        _fk('cohort_id', 'cohorts', 'CASCADE'),
        _fk('class_id', 'zeus_classes', 'CASCADE'),
        _fk('enrollment_id', 'cohort_enrollments', 'CASCADE'),
        _fk('created_by', 'users', 'SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_zeus_notifications')),
    )
    for column in ('cohort_id', 'class_id', 'enrollment_id', 'created_at'):
        op.create_index(op.f(f'ix_zeus_notifications_{column}'), 'zeus_notifications', [column], unique=False)


def downgrade():
    op.drop_table('zeus_notifications')
