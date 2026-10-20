"""staff profile: phone, photo, updated_at

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-21 16:00:00

"""
import sqlalchemy as sa
from alembic import op

revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('phone', sa.String(length=30), nullable=True))
        batch_op.add_column(sa.Column('photo', sa.LargeBinary(), nullable=True))
        batch_op.add_column(sa.Column('photo_type', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(),
                                      nullable=False))


def downgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('updated_at')
        batch_op.drop_column('photo_type')
        batch_op.drop_column('photo')
        batch_op.drop_column('phone')
