"""individual notifications: a notification sent to one student

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-03 15:00:00

"""
import sqlalchemy as sa
from alembic import op

revision = '0022'
down_revision = '0021'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.add_column(sa.Column('candidate_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_notifications_candidate_id'), ['candidate_id'], unique=False)
        batch_op.create_foreign_key(batch_op.f('fk_notifications_candidate_id_candidates'), 'candidates',
                                    ['candidate_id'], ['id'], ondelete='CASCADE')


def downgrade():
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_notifications_candidate_id_candidates'), type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_notifications_candidate_id'))
        batch_op.drop_column('candidate_id')
