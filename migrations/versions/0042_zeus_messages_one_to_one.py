"""zeus messaging one to one: a conversation is between a student and one coach of the class

Revision ID: 0042
Revises: 0041
Create Date: 2026-11-18 14:56:17.607886

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0042'
down_revision = '0041'
branch_labels = None
depends_on = None


def upgrade():
    # The class-team conversations of 0041 were never released, and one can't be given to a single coach:
    # start empty (cascades to messages and read markers).
    op.execute("DELETE FROM zeus_conversations")
    with op.batch_alter_table('zeus_conversations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('coach_id', sa.Integer(), nullable=False))
        batch_op.drop_constraint(batch_op.f('uq_zeus_conversations_class_enrollment'), type_='unique')
        batch_op.create_index(batch_op.f('ix_zeus_conversations_coach_id'), ['coach_id'], unique=False)
        batch_op.create_unique_constraint('uq_zeus_conversations_class_enrollment_coach',
                                          ['class_id', 'enrollment_id', 'coach_id'])
        batch_op.create_foreign_key(batch_op.f('fk_zeus_conversations_coach_id_users'), 'users', ['coach_id'], ['id'],
                                    ondelete='CASCADE')


def downgrade():
    # A student may have one conversation per coach: they don't fit the one-per-class constraint of 0041.
    op.execute("DELETE FROM zeus_conversations")
    with op.batch_alter_table('zeus_conversations', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_zeus_conversations_coach_id_users'), type_='foreignkey')
        batch_op.drop_constraint('uq_zeus_conversations_class_enrollment_coach', type_='unique')
        batch_op.drop_index(batch_op.f('ix_zeus_conversations_coach_id'))
        batch_op.create_unique_constraint(batch_op.f('uq_zeus_conversations_class_enrollment'),
                                          ['class_id', 'enrollment_id'])
        batch_op.drop_column('coach_id')
