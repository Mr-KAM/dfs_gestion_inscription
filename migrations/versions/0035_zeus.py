"""Zeus mode: classes (and invitations), courses, scheduled sessions, roll calls of the training cohorts

Revision ID: 0035
Revises: 0034
Create Date: 2026-11-12 14:00:00

"""
import sqlalchemy as sa
from alembic import op

revision = '0035'
down_revision = '0034'
branch_labels = None
depends_on = None


def _fk(table, column, target, ondelete):
    return sa.ForeignKeyConstraint([column], [f'{target}.id'], name=op.f(f'fk_{table}_{column}_{target}'),
                                   ondelete=ondelete)


def _index(table, *columns):
    for column in columns:
        op.create_index(op.f(f'ix_{table}_{column}'), table, [column], unique=False)


def upgrade():
    op.create_table(
        'zeus_classes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('cohort_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=80), nullable=False),
        sa.Column('coach_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        _fk('zeus_classes', 'cohort_id', 'cohorts', 'CASCADE'),
        _fk('zeus_classes', 'coach_id', 'users', 'SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_zeus_classes')),
        sa.UniqueConstraint('cohort_id', 'name', name='uq_zeus_classes_cohort_name'),
    )
    _index('zeus_classes', 'cohort_id')
    op.create_table(
        'zeus_class_members',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('class_id', sa.Integer(), nullable=False),
        sa.Column('enrollment_id', sa.Integer(), nullable=False),
        sa.Column('invited_by', sa.Integer(), nullable=True),
        sa.Column('invited_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('joined_at', sa.DateTime(timezone=True), nullable=True),
        _fk('zeus_class_members', 'class_id', 'zeus_classes', 'CASCADE'),
        _fk('zeus_class_members', 'enrollment_id', 'cohort_enrollments', 'CASCADE'),
        _fk('zeus_class_members', 'invited_by', 'users', 'SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_zeus_class_members')),
        sa.UniqueConstraint('class_id', 'enrollment_id', name='uq_zeus_class_members_class_enrollment'),
    )
    _index('zeus_class_members', 'class_id', 'enrollment_id')
    op.create_table(
        'zeus_courses',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('cohort_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=150), nullable=False),
        sa.Column('description', sa.String(length=255), nullable=True),
        sa.Column('hours', sa.Integer(), nullable=False),
        sa.Column('coach_id', sa.Integer(), nullable=True),
        _fk('zeus_courses', 'cohort_id', 'cohorts', 'CASCADE'),
        _fk('zeus_courses', 'coach_id', 'users', 'SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_zeus_courses')),
        sa.UniqueConstraint('cohort_id', 'name', name='uq_zeus_courses_cohort_name'),
    )
    _index('zeus_courses', 'cohort_id')
    op.create_table(
        'zeus_sessions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('course_id', sa.Integer(), nullable=False),
        sa.Column('class_id', sa.Integer(), nullable=True),
        sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ends_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('room', sa.String(length=100), nullable=True),
        sa.Column('coach_id', sa.Integer(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        _fk('zeus_sessions', 'course_id', 'zeus_courses', 'CASCADE'),
        _fk('zeus_sessions', 'class_id', 'zeus_classes', 'CASCADE'),
        _fk('zeus_sessions', 'coach_id', 'users', 'SET NULL'),
        _fk('zeus_sessions', 'created_by', 'users', 'SET NULL'),
        sa.CheckConstraint('ends_at > starts_at', name=op.f('ck_zeus_sessions_ends_after_start')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_zeus_sessions')),
    )
    _index('zeus_sessions', 'course_id', 'class_id', 'starts_at')
    op.create_table(
        'zeus_attendances',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('session_id', sa.Integer(), nullable=False),
        sa.Column('enrollment_id', sa.Integer(), nullable=False),
        sa.Column('method', sa.String(length=10), nullable=False),
        sa.Column('marked_by', sa.Integer(), nullable=True),
        sa.Column('marked_at', sa.DateTime(timezone=True), nullable=False),
        _fk('zeus_attendances', 'session_id', 'zeus_sessions', 'CASCADE'),
        _fk('zeus_attendances', 'enrollment_id', 'cohort_enrollments', 'CASCADE'),
        _fk('zeus_attendances', 'marked_by', 'users', 'SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_zeus_attendances')),
        sa.UniqueConstraint('session_id', 'enrollment_id', name='uq_zeus_attendances_session_enrollment'),
    )
    _index('zeus_attendances', 'session_id', 'enrollment_id')


def downgrade():
    for table in ('zeus_attendances', 'zeus_sessions', 'zeus_courses', 'zeus_class_members', 'zeus_classes'):
        op.drop_table(table)
