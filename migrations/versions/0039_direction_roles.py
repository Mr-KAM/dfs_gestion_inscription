"""new staff profiles: DG, DGA, directeur financier (+ adjoint), DAF, responsable bibliothèque

Revision ID: 0039
Revises: 0038
Create Date: 2026-11-15 10:00:00

"""
from alembic import op

revision = '0039'
down_revision = '0038'
branch_labels = None
depends_on = None

OLD_ROLES = ("'admin', 'supervisor', 'motivation_tester', 'technical_tester', 'coach', 'reception', 'residence', "
             "'candidate', 'trade_coach', 'partner', 'cme'")
NEW_ROLES = OLD_ROLES + ", 'dg', 'dga', 'df', 'dfa', 'daf', 'librarian'"


def _roles(roles):
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('ck_users_role'), type_='check')
        batch_op.create_check_constraint(batch_op.f('ck_users_role'), f"role IN ({roles})")


def upgrade():
    _roles(NEW_ROLES)


def downgrade():
    op.execute("DELETE FROM users WHERE role IN ('dg', 'dga', 'df', 'dfa', 'daf', 'librarian')")
    _roles(OLD_ROLES)
