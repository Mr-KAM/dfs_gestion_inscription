"""individual minutes: signature of the pedagogical and academic director, loaded on the global jury page

Revision ID: 0030
Revises: 0029
Create Date: 2026-11-07 20:00:00

"""
import sqlalchemy as sa
from alembic import op

revision = '0030'
down_revision = '0029'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('defenses', schema=None) as batch_op:
        batch_op.add_column(sa.Column('director_signature', sa.LargeBinary(), nullable=True))
        batch_op.add_column(sa.Column('director_signature_type', sa.String(length=20), nullable=True))


def downgrade():
    with op.batch_alter_table('defenses', schema=None) as batch_op:
        batch_op.drop_column('director_signature_type')
        batch_op.drop_column('director_signature')
