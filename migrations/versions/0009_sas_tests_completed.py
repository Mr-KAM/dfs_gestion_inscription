"""candidates already in a SAS: both tests completed and validated (as a new admission does)

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-19 16:00:00

"""
from alembic import op

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade():
    # Exam code required once motivation is completed: the matricule is unique and valid as a code.
    # ponytail: new admissions get a generated code (next_exam_code), not reachable from a migration.
    op.execute("UPDATE candidates SET exam_code = matricule WHERE sas_id IS NOT NULL AND exam_code IS NULL")
    op.execute("UPDATE candidates SET motivation_completed_at = COALESCE(motivation_completed_at, sas_admitted_at), "
               "technical_completed_at = COALESCE(technical_completed_at, sas_admitted_at), "
               "motivation_completed = true, technical_completed = true, "
               "motivation_passed = true, technical_passed = true WHERE sas_id IS NOT NULL")


def downgrade():
    pass  # data-only; previous values are not recorded
