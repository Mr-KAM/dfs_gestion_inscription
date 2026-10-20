"""SAS classes created by coaches (join code) and class invitations

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-21 14:00:00

"""
import sqlalchemy as sa
from alembic import op

revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'sas_classes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('sas_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=50), nullable=False),
        sa.Column('join_code', sa.String(length=12), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['sas_id'], ['sas.id'], name=op.f('fk_sas_classes_sas_id_sas'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_sas_classes_created_by_users'),
                                ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_sas_classes')),
        sa.UniqueConstraint('join_code', name=op.f('uq_sas_classes_join_code')),
        sa.UniqueConstraint('sas_id', 'name', name='uq_sas_classes_sas_name'),
    )
    op.create_index(op.f('ix_sas_classes_sas_id'), 'sas_classes', ['sas_id'], unique=False)
    op.create_table(
        'class_invitations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('class_id', sa.Integer(), nullable=False),
        sa.Column('candidate_id', sa.Integer(), nullable=False),
        sa.Column('invited_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('responded_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['class_id'], ['sas_classes.id'], name=op.f('fk_class_invitations_class_id_sas_classes'),
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'],
                                name=op.f('fk_class_invitations_candidate_id_candidates'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['invited_by'], ['users.id'], name=op.f('fk_class_invitations_invited_by_users'),
                                ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_class_invitations')),
        sa.UniqueConstraint('class_id', 'candidate_id', name='uq_class_invitations_class_candidate'),
    )
    op.create_index(op.f('ix_class_invitations_class_id'), 'class_invitations', ['class_id'], unique=False)
    op.create_index(op.f('ix_class_invitations_candidate_id'), 'class_invitations', ['candidate_id'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_class_invitations_candidate_id'), table_name='class_invitations')
    op.drop_index(op.f('ix_class_invitations_class_id'), table_name='class_invitations')
    op.drop_table('class_invitations')
    op.drop_index(op.f('ix_sas_classes_sas_id'), table_name='sas_classes')
    op.drop_table('sas_classes')
