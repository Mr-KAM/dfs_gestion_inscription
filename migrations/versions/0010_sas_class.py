"""SAS: class assigned to an admitted candidate

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-20 10:00:00

"""
import sqlalchemy as sa
from alembic import op

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('candidates', schema=None) as batch_op:
        batch_op.add_column(sa.Column('sas_class', sa.String(length=50), nullable=True))


def downgrade():
    with op.batch_alter_table('candidates', schema=None) as batch_op:
        batch_op.drop_column('sas_class')
