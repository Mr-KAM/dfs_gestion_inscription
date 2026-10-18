"""motivation / technical test validated checkboxes

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-18 10:00:00

"""
import sqlalchemy as sa
from alembic import op

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('candidates', schema=None) as batch_op:
        batch_op.add_column(sa.Column('motivation_passed', sa.Boolean(), server_default=sa.false(), nullable=False))
        batch_op.add_column(sa.Column('technical_passed', sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade():
    with op.batch_alter_table('candidates', schema=None) as batch_op:
        batch_op.drop_column('technical_passed')
        batch_op.drop_column('motivation_passed')
