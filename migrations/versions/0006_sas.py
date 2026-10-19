"""SAS: next recruitment stage, admitted candidates with bedroom and equipment

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-19 10:00:00

"""
import sqlalchemy as sa
from alembic import op

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'sas',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('description', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_sas')),
        sa.UniqueConstraint('name', name=op.f('uq_sas_name')),
    )
    with op.batch_alter_table('candidates', schema=None) as batch_op:
        batch_op.add_column(sa.Column('sas_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('sas_admitted_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('sas_bedroom', sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column('sas_equipment', sa.String(length=255), nullable=True))
        batch_op.create_index(batch_op.f('ix_candidates_sas_id'), ['sas_id'], unique=False)
        batch_op.create_foreign_key(batch_op.f('fk_candidates_sas_id_sas'), 'sas', ['sas_id'], ['id'],
                                    ondelete='SET NULL')


def downgrade():
    with op.batch_alter_table('candidates', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_candidates_sas_id_sas'), type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_candidates_sas_id'))
        batch_op.drop_column('sas_equipment')
        batch_op.drop_column('sas_bedroom')
        batch_op.drop_column('sas_admitted_at')
        batch_op.drop_column('sas_id')
    op.drop_table('sas')
