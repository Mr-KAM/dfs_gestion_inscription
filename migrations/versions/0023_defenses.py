"""SAS defenses: juries, jury members (one jury per person and defense), students heard by each jury

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-04 10:00:00

"""
import sqlalchemy as sa
from alembic import op

revision = '0023'
down_revision = '0022'
branch_labels = None
depends_on = None


def _fk(table, column, target, ondelete):
    return sa.ForeignKeyConstraint([column], [f'{target}.id'], name=op.f(f'fk_{table}_{column}_{target}'),
                                   ondelete=ondelete)


def upgrade():
    op.create_table(
        'defenses',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('sas_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=150), nullable=False),
        sa.Column('starts_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('location', sa.String(length=150), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        _fk('defenses', 'sas_id', 'sas', 'CASCADE'),
        _fk('defenses', 'created_by', 'users', 'SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_defenses')),
    )
    op.create_index(op.f('ix_defenses_sas_id'), 'defenses', ['sas_id'], unique=False)
    op.create_table(
        'juries',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('defense_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('room', sa.String(length=100), nullable=True),
        sa.Column('starts_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('duration', sa.Integer(), nullable=False),
        _fk('juries', 'defense_id', 'defenses', 'CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_juries')),
    )
    op.create_index(op.f('ix_juries_defense_id'), 'juries', ['defense_id'], unique=False)
    op.create_table(
        'jury_members',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('jury_id', sa.Integer(), nullable=False),
        sa.Column('defense_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('role', sa.String(length=20), nullable=False),
        _fk('jury_members', 'jury_id', 'juries', 'CASCADE'),
        _fk('jury_members', 'defense_id', 'defenses', 'CASCADE'),
        _fk('jury_members', 'user_id', 'users', 'CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_jury_members')),
        sa.UniqueConstraint('defense_id', 'user_id', name='uq_jury_members_defense_user'),
    )
    op.create_index(op.f('ix_jury_members_jury_id'), 'jury_members', ['jury_id'], unique=False)
    op.create_index(op.f('ix_jury_members_user_id'), 'jury_members', ['user_id'], unique=False)
    op.create_table(
        'jury_candidates',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('jury_id', sa.Integer(), nullable=False),
        sa.Column('defense_id', sa.Integer(), nullable=False),
        sa.Column('candidate_id', sa.Integer(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        _fk('jury_candidates', 'jury_id', 'juries', 'CASCADE'),
        _fk('jury_candidates', 'defense_id', 'defenses', 'CASCADE'),
        _fk('jury_candidates', 'candidate_id', 'candidates', 'CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_jury_candidates')),
        sa.UniqueConstraint('defense_id', 'candidate_id', name='uq_jury_candidates_defense_candidate'),
    )
    op.create_index(op.f('ix_jury_candidates_jury_id'), 'jury_candidates', ['jury_id'], unique=False)
    op.create_index(op.f('ix_jury_candidates_candidate_id'), 'jury_candidates', ['candidate_id'], unique=False)


def downgrade():
    op.drop_table('jury_candidates')
    op.drop_table('jury_members')
    op.drop_table('juries')
    op.drop_table('defenses')
