"""new staff profiles: coach métier, partenaire, conseiller médiation emploi (CME)

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-04 18:00:00

"""
from alembic import op

revision = '0025'
down_revision = '0024'
branch_labels = None
depends_on = None

OLD_ROLES = ("'admin', 'supervisor', 'motivation_tester', 'technical_tester', 'coach', 'reception', 'residence', "
             "'candidate'")
NEW_ROLES = OLD_ROLES + ", 'trade_coach', 'partner', 'cme'"


def _roles(roles):
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('ck_users_role'), type_='check')
        batch_op.create_check_constraint(batch_op.f('ck_users_role'), f"role IN ({roles})")


def upgrade():
    _roles(NEW_ROLES)


def downgrade():
    op.execute("DELETE FROM users WHERE role IN ('trade_coach', 'partner', 'cme')")
    _roles(OLD_ROLES)
