"""gender of staff accounts (team export)

Revision ID: 0038
Revises: 0037
Create Date: 2026-11-14 10:00:00

"""
import sqlalchemy as sa
from alembic import op

revision = '0038'
down_revision = '0037'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('gender', sa.String(length=1), nullable=True))


def downgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('gender')
