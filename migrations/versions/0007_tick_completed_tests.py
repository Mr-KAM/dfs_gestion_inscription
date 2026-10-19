"""tick 'validated' for every test already completed (new default since completion ticks it)

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-19 12:00:00

"""
from alembic import op

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE candidates SET motivation_passed = true WHERE motivation_completed AND NOT motivation_passed")
    op.execute("UPDATE candidates SET technical_passed = true WHERE technical_completed AND NOT technical_passed")


def downgrade():
    pass  # ponytail: data-only; which boxes were unticked before is not recorded
