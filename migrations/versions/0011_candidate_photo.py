"""candidate profile photo

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-20 14:00:00

"""
import sqlalchemy as sa
from alembic import op

revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('candidates', schema=None) as batch_op:
        batch_op.add_column(sa.Column('photo', sa.LargeBinary(), nullable=True))
        batch_op.add_column(sa.Column('photo_type', sa.String(length=20), nullable=True))


def downgrade():
    with op.batch_alter_table('candidates', schema=None) as batch_op:
        batch_op.drop_column('photo_type')
        batch_op.drop_column('photo')
