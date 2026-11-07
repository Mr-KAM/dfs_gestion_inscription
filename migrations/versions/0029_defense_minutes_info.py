"""general minutes: session president, date, place and stamp, set by the admin on the global jury page

Revision ID: 0029
Revises: 0028
Create Date: 2026-11-07 18:00:00

"""
import sqlalchemy as sa
from alembic import op

revision = '0029'
down_revision = '0028'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('defenses', schema=None) as batch_op:
        batch_op.add_column(sa.Column('minutes_info', sa.JSON(), nullable=False, server_default='{}'))
        batch_op.add_column(sa.Column('stamp', sa.LargeBinary(), nullable=True))
        batch_op.add_column(sa.Column('stamp_type', sa.String(length=20), nullable=True))


def downgrade():
    with op.batch_alter_table('defenses', schema=None) as batch_op:
        batch_op.drop_column('stamp_type')
        batch_op.drop_column('stamp')
        batch_op.drop_column('minutes_info')
