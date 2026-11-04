"""defense sheets: members' ratings, shared sheet, deliberation, jury timer, staff signatures

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-04 15:00:00

"""
import sqlalchemy as sa
from alembic import op

revision = '0024'
down_revision = '0023'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('signature', sa.LargeBinary(), nullable=True))
        batch_op.add_column(sa.Column('signature_type', sa.String(length=20), nullable=True))
    with op.batch_alter_table('juries', schema=None) as batch_op:
        batch_op.add_column(sa.Column('validated_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('validated_by', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('timer_ends_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('timer_left', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(batch_op.f('fk_juries_validated_by_users'), 'users', ['validated_by'], ['id'],
                                    ondelete='SET NULL')
    with op.batch_alter_table('jury_candidates', schema=None) as batch_op:
        batch_op.add_column(sa.Column('sheet', sa.JSON(), server_default='{}', nullable=False))
    op.create_table(
        'defense_scores',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('jury_candidate_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('ratings', sa.JSON(), nullable=False),
        sa.Column('evidence', sa.JSON(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['jury_candidate_id'], ['jury_candidates.id'],
                                name=op.f('fk_defense_scores_jury_candidate_id_jury_candidates'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_defense_scores_user_id_users'),
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_defense_scores')),
        sa.UniqueConstraint('jury_candidate_id', 'user_id', name='uq_defense_scores_row_user'),
    )
    op.create_index(op.f('ix_defense_scores_jury_candidate_id'), 'defense_scores', ['jury_candidate_id'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_defense_scores_jury_candidate_id'), table_name='defense_scores')
    op.drop_table('defense_scores')
    with op.batch_alter_table('jury_candidates', schema=None) as batch_op:
        batch_op.drop_column('sheet')
    with op.batch_alter_table('juries', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_juries_validated_by_users'), type_='foreignkey')
        batch_op.drop_column('timer_left')
        batch_op.drop_column('timer_ends_at')
        batch_op.drop_column('validated_by')
        batch_op.drop_column('validated_at')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('signature_type')
        batch_op.drop_column('signature')
