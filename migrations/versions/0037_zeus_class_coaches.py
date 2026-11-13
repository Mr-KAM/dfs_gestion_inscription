"""Zeus classes: description, teaching staff (coaches with a function, managers) and the catalogue of class roles.
The single coach of each existing class becomes its manager.

Revision ID: 0037
Revises: 0036
Create Date: 2026-11-13 15:00:00

"""
import sqlalchemy as sa
from alembic import op

revision = '0037'
down_revision = '0036'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'zeus_class_roles',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=80), nullable=False),
        sa.Column('description', sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_zeus_class_roles')),
        sa.UniqueConstraint('name', name=op.f('uq_zeus_class_roles_name')),
    )
    op.create_table(
        'zeus_class_coaches',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('class_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('role_id', sa.Integer(), nullable=True),
        sa.Column('is_manager', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('invited_by', sa.Integer(), nullable=True),
        sa.Column('invited_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['class_id'], ['zeus_classes.id'], name=op.f('fk_zeus_class_coaches_class_id_zeus_classes'),
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_zeus_class_coaches_user_id_users'),
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['role_id'], ['zeus_class_roles.id'],
                                name=op.f('fk_zeus_class_coaches_role_id_zeus_class_roles'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['invited_by'], ['users.id'], name=op.f('fk_zeus_class_coaches_invited_by_users'),
                                ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_zeus_class_coaches')),
        sa.UniqueConstraint('class_id', 'user_id', name='uq_zeus_class_coaches_class_user'),
    )
    op.create_index(op.f('ix_zeus_class_coaches_class_id'), 'zeus_class_coaches', ['class_id'], unique=False)
    op.create_index(op.f('ix_zeus_class_coaches_user_id'), 'zeus_class_coaches', ['user_id'], unique=False)
    # The coach who created each class becomes its manager (accepted: he created it).
    op.execute("INSERT INTO zeus_class_coaches (class_id, user_id, is_manager, invited_at, accepted_at) "
               "SELECT id, coach_id, true, created_at, created_at FROM zeus_classes WHERE coach_id IS NOT NULL")
    with op.batch_alter_table('zeus_classes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('description', sa.String(length=255), nullable=True))
        batch_op.drop_constraint('fk_zeus_classes_coach_id_users', type_='foreignkey')
        batch_op.drop_column('coach_id')


def downgrade():
    with op.batch_alter_table('zeus_classes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('coach_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_zeus_classes_coach_id_users', 'users', ['coach_id'], ['id'], ondelete='SET NULL')
        batch_op.drop_column('description')
    op.execute("UPDATE zeus_classes SET coach_id = (SELECT MIN(user_id) FROM zeus_class_coaches c "
               "WHERE c.class_id = zeus_classes.id AND c.is_manager)")
    op.drop_table('zeus_class_coaches')
    op.drop_table('zeus_class_roles')
