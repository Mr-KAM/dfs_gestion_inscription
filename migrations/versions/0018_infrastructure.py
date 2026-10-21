"""infrastructure and inventory: buildings, levels, spaces, equipment, consumables and stock movements

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-21 16:00:00

"""
import sqlalchemy as sa
from alembic import op

revision = '0018'
down_revision = '0017'
branch_labels = None
depends_on = None


def _fk(table, column, target, ondelete):
    return sa.ForeignKeyConstraint([column], [f'{target}.id'], name=op.f(f'fk_{table}_{column}_{target}'),
                                   ondelete=ondelete)


def upgrade():
    op.create_table(
        'buildings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('description', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_buildings')),
        sa.UniqueConstraint('name', name=op.f('uq_buildings_name')),
    )
    op.create_table(
        'levels',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('building_id', sa.Integer(), nullable=False),
        sa.Column('number', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=True),
        _fk('levels', 'building_id', 'buildings', 'CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_levels')),
        sa.UniqueConstraint('building_id', 'number', name='uq_levels_building_number'),
    )
    op.create_index(op.f('ix_levels_building_id'), 'levels', ['building_id'], unique=False)
    op.create_table(
        'spaces',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('level_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('usage', sa.String(length=40), nullable=False),
        sa.Column('capacity', sa.Integer(), nullable=True),
        sa.Column('description', sa.String(length=255), nullable=True),
        _fk('spaces', 'level_id', 'levels', 'CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_spaces')),
        sa.UniqueConstraint('level_id', 'name', name='uq_spaces_level_name'),
    )
    op.create_index(op.f('ix_spaces_level_id'), 'spaces', ['level_id'], unique=False)
    op.create_table(
        'equipment',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=30), nullable=False),
        sa.Column('name', sa.String(length=150), nullable=False),
        sa.Column('category', sa.String(length=40), nullable=False),
        sa.Column('brand', sa.String(length=100), nullable=True),
        sa.Column('serial_number', sa.String(length=100), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('space_id', sa.Integer(), nullable=True),
        sa.Column('assigned_to', sa.Integer(), nullable=True),
        sa.Column('acquired_on', sa.Date(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        _fk('equipment', 'space_id', 'spaces', 'SET NULL'),
        _fk('equipment', 'assigned_to', 'users', 'SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_equipment')),
        sa.UniqueConstraint('code', name=op.f('uq_equipment_code')),
    )
    op.create_index(op.f('ix_equipment_space_id'), 'equipment', ['space_id'], unique=False)
    op.create_index(op.f('ix_equipment_assigned_to'), 'equipment', ['assigned_to'], unique=False)
    op.create_table(
        'consumables',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=150), nullable=False),
        sa.Column('category', sa.String(length=40), nullable=False),
        sa.Column('unit', sa.String(length=20), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('min_quantity', sa.Integer(), nullable=False),
        sa.Column('space_id', sa.Integer(), nullable=True),
        _fk('consumables', 'space_id', 'spaces', 'SET NULL'),
        sa.CheckConstraint('quantity >= 0', name=op.f('ck_consumables_quantity_positive')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_consumables')),
        sa.UniqueConstraint('name', name=op.f('uq_consumables_name')),
    )
    op.create_index(op.f('ix_consumables_space_id'), 'consumables', ['space_id'], unique=False)
    op.create_table(
        'consumable_movements',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('consumable_id', sa.Integer(), nullable=False),
        sa.Column('delta', sa.Integer(), nullable=False),
        sa.Column('balance', sa.Integer(), nullable=False),
        sa.Column('note', sa.String(length=255), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        _fk('consumable_movements', 'consumable_id', 'consumables', 'CASCADE'),
        _fk('consumable_movements', 'created_by', 'users', 'SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_consumable_movements')),
    )
    op.create_index(op.f('ix_consumable_movements_consumable_id'), 'consumable_movements', ['consumable_id'],
                    unique=False)


def downgrade():
    for table, columns in (('consumable_movements', ['consumable_id']), ('consumables', ['space_id']),
                           ('equipment', ['assigned_to', 'space_id']), ('spaces', ['level_id']),
                           ('levels', ['building_id']), ('buildings', [])):
        for column in columns:
            op.drop_index(op.f(f'ix_{table}_{column}'), table_name=table)
        op.drop_table(table)
