"""defense results reach the students only when the admin sends them

Revision ID: 0028
Revises: 0027
Create Date: 2026-11-06 18:00:00

"""
import sqlalchemy as sa
from alembic import op

revision = '0028'
down_revision = '0027'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('defenses', schema=None) as batch_op:
        batch_op.add_column(sa.Column('results_sent_at', sa.DateTime(timezone=True), nullable=True))


def downgrade():
    with op.batch_alter_table('defenses', schema=None) as batch_op:
        batch_op.drop_column('results_sent_at')
