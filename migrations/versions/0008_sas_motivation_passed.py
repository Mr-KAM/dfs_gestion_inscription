"""mark the motivation test as validated for every candidate admitted to a SAS

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-19 14:00:00

"""
from alembic import op

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE candidates SET motivation_passed = true WHERE sas_id IS NOT NULL AND NOT motivation_passed")


def downgrade():
    pass  # ponytail: data-only; previous values are not recorded
