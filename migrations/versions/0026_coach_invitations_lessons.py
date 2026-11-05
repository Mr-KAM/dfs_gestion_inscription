"""coach métier invitations into classes, coaches' program calendar (lessons)

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-05 10:00:00

"""
import sqlalchemy as sa
from alembic import op

revision = '0026'
down_revision = '0025'
branch_labels = None
depends_on = None


def _fk(table, column, target, ondelete):
    return sa.ForeignKeyConstraint([column], [f'{target}.id'], name=op.f(f'fk_{table}_{column}_{target}'),
                                   ondelete=ondelete)


def upgrade():
    op.create_table(
        'coach_invitations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('class_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('invited_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('responded_at', sa.DateTime(timezone=True), nullable=True),
        _fk('coach_invitations', 'class_id', 'sas_classes', 'CASCADE'),
        _fk('coach_invitations', 'user_id', 'users', 'CASCADE'),
        _fk('coach_invitations', 'invited_by', 'users', 'SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_coach_invitations')),
        sa.UniqueConstraint('class_id', 'user_id', name='uq_coach_invitations_class_user'),
    )
    op.create_index(op.f('ix_coach_invitations_class_id'), 'coach_invitations', ['class_id'], unique=False)
    op.create_index(op.f('ix_coach_invitations_user_id'), 'coach_invitations', ['user_id'], unique=False)
    op.create_table(
        'lessons',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('sas_id', sa.Integer(), nullable=False),
        sa.Column('class_name', sa.String(length=50), nullable=False),
        sa.Column('coach_id', sa.Integer(), nullable=True),
        sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('duration', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=150), nullable=False),
        sa.Column('module', sa.String(length=150), nullable=False),
        sa.Column('objectives', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('report', sa.Text(), nullable=False),
        sa.Column('attendance_session_id', sa.Integer(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        _fk('lessons', 'sas_id', 'sas', 'CASCADE'),
        _fk('lessons', 'coach_id', 'users', 'SET NULL'),
        _fk('lessons', 'attendance_session_id', 'attendance_sessions', 'SET NULL'),
        _fk('lessons', 'created_by', 'users', 'SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_lessons')),
    )
    for column in ('sas_id', 'coach_id', 'starts_at'):
        op.create_index(op.f(f'ix_lessons_{column}'), 'lessons', [column], unique=False)


def downgrade():
    for column in ('sas_id', 'coach_id', 'starts_at'):
        op.drop_index(op.f(f'ix_lessons_{column}'), table_name='lessons')
    op.drop_table('lessons')
    op.drop_index(op.f('ix_coach_invitations_user_id'), table_name='coach_invitations')
    op.drop_index(op.f('ix_coach_invitations_class_id'), table_name='coach_invitations')
    op.drop_table('coach_invitations')
