"""color of the personal message giving a student his defense decision

Revision ID: 0031
Revises: 0030
Create Date: 2026-11-07 22:00:00

"""
import sqlalchemy as sa
from alembic import op

revision = '0031'
down_revision = '0030'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.add_column(sa.Column('level', sa.String(length=10), nullable=True))


def downgrade():
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.drop_column('level')
