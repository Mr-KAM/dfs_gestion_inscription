"""add supervisor role

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-02 10:00:00

"""
from alembic import op

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None

OLD = "role IN ('admin', 'motivation_tester', 'technical_tester')"
NEW = "role IN ('admin', 'supervisor', 'motivation_tester', 'technical_tester')"


def upgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_constraint(op.f('ck_users_role'), type_='check')
        batch_op.create_check_constraint(op.f('ck_users_role'), NEW)


def downgrade():
    # Supervisors must be reassigned before the old constraint can be restored.
    op.execute("UPDATE users SET role = 'motivation_tester', active = false WHERE role = 'supervisor'")
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_constraint(op.f('ck_users_role'), type_='check')
        batch_op.create_check_constraint(op.f('ck_users_role'), OLD)
