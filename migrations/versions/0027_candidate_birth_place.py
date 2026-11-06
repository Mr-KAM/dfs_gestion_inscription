"""candidate place of birth

Revision ID: 0027
Revises: 0026
Create Date: 2026-11-06 10:00:00

"""
import sqlalchemy as sa
from alembic import op

revision = '0027'
down_revision = '0026'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('candidates', schema=None) as batch_op:
        batch_op.add_column(sa.Column('birth_place', sa.String(length=120), nullable=True))


def downgrade():
    with op.batch_alter_table('candidates', schema=None) as batch_op:
        batch_op.drop_column('birth_place')
