"""SAS check-in periods (pointage) and attendance marks

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-20 16:00:00

"""
import sqlalchemy as sa
from alembic import op

revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'attendance_sessions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('sas_id', sa.Integer(), nullable=False),
        sa.Column('class_name', sa.String(length=50), nullable=False),
        sa.Column('label', sa.String(length=100), nullable=False),
        sa.Column('opened_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('closes_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['sas_id'], ['sas.id'], name=op.f('fk_attendance_sessions_sas_id_sas'),
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_attendance_sessions_created_by_users'),
                                ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_attendance_sessions')),
    )
    op.create_index(op.f('ix_attendance_sessions_sas_id'), 'attendance_sessions', ['sas_id'], unique=False)
    op.create_table(
        'attendances',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('session_id', sa.Integer(), nullable=False),
        sa.Column('candidate_id', sa.Integer(), nullable=False),
        sa.Column('marked_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('method', sa.String(length=20), nullable=False),
        sa.Column('marked_by', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['session_id'], ['attendance_sessions.id'],
                                name=op.f('fk_attendances_session_id_attendance_sessions'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], name=op.f('fk_attendances_candidate_id_candidates'),
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['marked_by'], ['users.id'], name=op.f('fk_attendances_marked_by_users'),
                                ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_attendances')),
        sa.UniqueConstraint('session_id', 'candidate_id', name='uq_attendances_session_candidate'),
    )
    op.create_index(op.f('ix_attendances_session_id'), 'attendances', ['session_id'], unique=False)
    op.create_index(op.f('ix_attendances_candidate_id'), 'attendances', ['candidate_id'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_attendances_candidate_id'), table_name='attendances')
    op.drop_index(op.f('ix_attendances_session_id'), table_name='attendances')
    op.drop_table('attendances')
    op.drop_index(op.f('ix_attendance_sessions_sas_id'), table_name='attendance_sessions')
    op.drop_table('attendance_sessions')
