"""password reset requests (forgot password from the login pages, handled by an admin)

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-23 18:00:00

"""
import sqlalchemy as sa
from alembic import op

revision = '0020'
down_revision = '0019'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'password_reset_requests',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('message', sa.String(length=255), nullable=True),
        sa.Column('ip_address', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('handled_by', sa.Integer(), nullable=True),
        sa.Column('handled_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_password_reset_requests_user_id_users'),
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['handled_by'], ['users.id'],
                                name=op.f('fk_password_reset_requests_handled_by_users'), ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_password_reset_requests')),
    )
    op.create_index(op.f('ix_password_reset_requests_user_id'), 'password_reset_requests', ['user_id'], unique=False)
    op.create_index(op.f('ix_password_reset_requests_status'), 'password_reset_requests', ['status'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_password_reset_requests_status'), table_name='password_reset_requests')
    op.drop_index(op.f('ix_password_reset_requests_user_id'), table_name='password_reset_requests')
    op.drop_table('password_reset_requests')
