"""roll call: hours counted (absence hours when not marked in time)

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-21 10:00:00

"""
import sqlalchemy as sa
from alembic import op

revision = '0017'
down_revision = '0016'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('attendance_sessions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('hours', sa.Integer(), server_default='1', nullable=False))


def downgrade():
    with op.batch_alter_table('attendance_sessions', schema=None) as batch_op:
        batch_op.drop_column('hours')
