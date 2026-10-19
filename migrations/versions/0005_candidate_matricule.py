"""candidate matricule (26CAND000001)

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-19 10:00:00

"""
from datetime import date

import sqlalchemy as sa
from alembic import op

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('candidates', schema=None) as batch_op:
        batch_op.add_column(sa.Column('matricule', sa.String(length=12), nullable=True))
    # Existing candidates: numbered in creation order (id), with the current year.
    conn = op.get_bind()
    prefix = f"{date.today():%y}CAND"
    ids = conn.execute(sa.text("SELECT id FROM candidates ORDER BY id")).scalars().all()
    if ids:
        conn.execute(sa.text("UPDATE candidates SET matricule = :m WHERE id = :id"),
                     [{"m": f"{prefix}{n:06d}", "id": i} for n, i in enumerate(ids, start=1)])
    with op.batch_alter_table('candidates', schema=None) as batch_op:
        batch_op.alter_column('matricule', existing_type=sa.String(length=12), nullable=False)
        batch_op.create_unique_constraint(batch_op.f('uq_candidates_matricule'), ['matricule'])


def downgrade():
    with op.batch_alter_table('candidates', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('uq_candidates_matricule'), type_='unique')
        batch_op.drop_column('matricule')
