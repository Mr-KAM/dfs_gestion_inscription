"""site register: visit or other activity, origin, ID type and number

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-22 10:00:00

"""
import sqlalchemy as sa
from alembic import op

revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None

COLUMNS = (('origin', 120), ('id_type', 40), ('id_number', 50), ('activity_category', 40), ('activity', 255))


def upgrade():
    with op.batch_alter_table('visits', schema=None) as batch_op:
        batch_op.add_column(sa.Column('kind', sa.String(length=20), server_default='visit', nullable=False))
        for name, length in COLUMNS:
            batch_op.add_column(sa.Column(name, sa.String(length=length), nullable=True))


def downgrade():
    with op.batch_alter_table('visits', schema=None) as batch_op:
        for name, _length in reversed(COLUMNS):
            batch_op.drop_column(name)
        batch_op.drop_column('kind')
