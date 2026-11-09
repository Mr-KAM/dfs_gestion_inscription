"""public results link of a defense: token and on/off

Revision ID: 0032
Revises: 0031
Create Date: 2026-11-09 18:00:00

"""
import sqlalchemy as sa
from alembic import op

revision = '0032'
down_revision = '0031'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('defenses', schema=None) as batch_op:
        batch_op.add_column(sa.Column('public_token', sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column('public_enabled', sa.Boolean(), server_default=sa.false(), nullable=False))
        batch_op.create_unique_constraint('uq_defenses_public_token', ['public_token'])


def downgrade():
    with op.batch_alter_table('defenses', schema=None) as batch_op:
        batch_op.drop_constraint('uq_defenses_public_token', type_='unique')
        batch_op.drop_column('public_enabled')
        batch_op.drop_column('public_token')
