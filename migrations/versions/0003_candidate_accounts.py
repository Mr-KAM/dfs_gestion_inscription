"""candidate accounts (login to the candidate space)

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-10 16:00:00

"""
import sqlalchemy as sa
from alembic import op

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None

STAFF = "'admin', 'supervisor', 'motivation_tester', 'technical_tester'"


def upgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('must_change_password', sa.Boolean(), server_default=sa.text('false'),
                                      nullable=False))
        batch_op.add_column(sa.Column('candidate_id', sa.Integer(), nullable=True))
        batch_op.create_unique_constraint(batch_op.f('uq_users_candidate_id'), ['candidate_id'])
        batch_op.create_foreign_key(batch_op.f('fk_users_candidate_id_candidates'), 'candidates', ['candidate_id'],
                                    ['id'], ondelete='CASCADE')
        batch_op.drop_constraint(batch_op.f('ck_users_role'), type_='check')
        batch_op.create_check_constraint(batch_op.f('ck_users_role'), f"role IN ({STAFF}, 'candidate')")
        batch_op.create_check_constraint(batch_op.f('ck_users_candidate_link'),
                                         "(role = 'candidate') = (candidate_id IS NOT NULL)")
    # Accounts themselves are created by `flask create-candidate-accounts` (run by entrypoint.sh).


def downgrade():
    op.execute("DELETE FROM users WHERE role = 'candidate'")
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('ck_users_candidate_link'), type_='check')
        batch_op.drop_constraint(batch_op.f('ck_users_role'), type_='check')
        batch_op.create_check_constraint(batch_op.f('ck_users_role'), f"role IN ({STAFF})")
        batch_op.drop_constraint(batch_op.f('fk_users_candidate_id_candidates'), type_='foreignkey')
        batch_op.drop_constraint(batch_op.f('uq_users_candidate_id'), type_='unique')
        batch_op.drop_column('candidate_id')
        batch_op.drop_column('must_change_password')
