"""campus roles (coach, reception, residence), coach classes, notifications, exercises, visits, incidents

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-21 10:00:00

"""
import sqlalchemy as sa
from alembic import op

revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None

OLD_ROLES = "'admin', 'supervisor', 'motivation_tester', 'technical_tester', 'candidate'"
NEW_ROLES = OLD_ROLES + ", 'coach', 'reception', 'residence'"


def _fk(table, column, target, ondelete):
    return sa.ForeignKeyConstraint([column], [f'{target}.id'], name=op.f(f'fk_{table}_{column}_{target}'), ondelete=ondelete)


def upgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('coach_classes', sa.JSON(), server_default='[]', nullable=False))
        batch_op.drop_constraint(batch_op.f('ck_users_role'), type_='check')
        batch_op.create_check_constraint(batch_op.f('ck_users_role'), f"role IN ({NEW_ROLES})")

    op.create_table(
        'notifications',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('sas_id', sa.Integer(), nullable=False),
        sa.Column('class_name', sa.String(length=50), nullable=False),
        sa.Column('title', sa.String(length=150), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        _fk('notifications', 'sas_id', 'sas', 'CASCADE'),
        _fk('notifications', 'created_by', 'users', 'SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_notifications')),
    )
    op.create_index(op.f('ix_notifications_sas_id'), 'notifications', ['sas_id'], unique=False)

    op.create_table(
        'exercises',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('sas_id', sa.Integer(), nullable=False),
        sa.Column('class_name', sa.String(length=50), nullable=False),
        sa.Column('title', sa.String(length=150), nullable=False),
        sa.Column('instructions', sa.Text(), nullable=False),
        sa.Column('opens_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('due_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        _fk('exercises', 'sas_id', 'sas', 'CASCADE'),
        _fk('exercises', 'created_by', 'users', 'SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_exercises')),
    )
    op.create_index(op.f('ix_exercises_sas_id'), 'exercises', ['sas_id'], unique=False)

    op.create_table(
        'exercise_submissions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('exercise_id', sa.Integer(), nullable=False),
        sa.Column('candidate_id', sa.Integer(), nullable=False),
        sa.Column('answer', sa.Text(), nullable=False),
        sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('score', sa.String(length=20), nullable=True),
        sa.Column('feedback', sa.Text(), nullable=True),
        sa.Column('reviewed_by', sa.Integer(), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
        _fk('exercise_submissions', 'exercise_id', 'exercises', 'CASCADE'),
        _fk('exercise_submissions', 'candidate_id', 'candidates', 'CASCADE'),
        _fk('exercise_submissions', 'reviewed_by', 'users', 'SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_exercise_submissions')),
        sa.UniqueConstraint('exercise_id', 'candidate_id', name='uq_exercise_submissions_exercise_candidate'),
    )
    op.create_index(op.f('ix_exercise_submissions_exercise_id'), 'exercise_submissions', ['exercise_id'], unique=False)
    op.create_index(op.f('ix_exercise_submissions_candidate_id'), 'exercise_submissions', ['candidate_id'], unique=False)

    op.create_table(
        'visits',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('visitor_name', sa.String(length=150), nullable=False),
        sa.Column('visitor_phone', sa.String(length=30), nullable=True),
        sa.Column('candidate_id', sa.Integer(), nullable=True),
        sa.Column('reason', sa.String(length=255), nullable=True),
        sa.Column('arrived_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('left_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        _fk('visits', 'candidate_id', 'candidates', 'SET NULL'),
        _fk('visits', 'created_by', 'users', 'SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_visits')),
    )
    op.create_index(op.f('ix_visits_candidate_id'), 'visits', ['candidate_id'], unique=False)

    op.create_table(
        'incidents',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('candidate_id', sa.Integer(), nullable=False),
        sa.Column('category', sa.String(length=20), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resolution', sa.Text(), nullable=True),
        sa.Column('resolved_by', sa.Integer(), nullable=True),
        _fk('incidents', 'candidate_id', 'candidates', 'CASCADE'),
        _fk('incidents', 'created_by', 'users', 'SET NULL'),
        _fk('incidents', 'resolved_by', 'users', 'SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_incidents')),
    )
    op.create_index(op.f('ix_incidents_candidate_id'), 'incidents', ['candidate_id'], unique=False)


def downgrade():
    for table, columns in (('incidents', ['candidate_id']), ('visits', ['candidate_id']),
                           ('exercise_submissions', ['candidate_id', 'exercise_id']), ('exercises', ['sas_id']),
                           ('notifications', ['sas_id'])):
        for column in columns:
            op.drop_index(op.f(f'ix_{table}_{column}'), table_name=table)
        op.drop_table(table)
    op.execute("DELETE FROM users WHERE role IN ('coach', 'reception', 'residence')")
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('ck_users_role'), type_='check')
        batch_op.create_check_constraint(batch_op.f('ck_users_role'), f"role IN ({OLD_ROLES})")
        batch_op.drop_column('coach_classes')
