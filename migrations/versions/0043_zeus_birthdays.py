"""zeus birthdays: staff birthday (day, month), opt-out, prompt snooze; birthday wishes

Revision ID: 0043
Revises: 0042
Create Date: 2026-11-18 15:45:12.082558

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0043'
down_revision = '0042'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('zeus_birthday_wishes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('recipient_id', sa.Integer(), nullable=False),
    sa.Column('author_id', sa.Integer(), nullable=False),
    sa.Column('birthday', sa.Date(), nullable=False),
    sa.Column('body', sa.String(length=280), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['author_id'], ['users.id'], name=op.f('fk_zeus_birthday_wishes_author_id_users'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['recipient_id'], ['users.id'], name=op.f('fk_zeus_birthday_wishes_recipient_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_zeus_birthday_wishes')),
    sa.UniqueConstraint('recipient_id', 'author_id', 'birthday', name='uq_zeus_birthday_wishes_recipient_author_birthday')
    )
    with op.batch_alter_table('zeus_birthday_wishes', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_zeus_birthday_wishes_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_zeus_birthday_wishes_recipient_id'), ['recipient_id'], unique=False)

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('birthday_day', sa.SmallInteger(), nullable=True))
        batch_op.add_column(sa.Column('birthday_month', sa.SmallInteger(), nullable=True))
        batch_op.add_column(sa.Column('hide_birthday', sa.Boolean(), server_default=sa.false(), nullable=False))
        batch_op.add_column(sa.Column('birthday_prompt_snoozed_until', sa.Date(), nullable=True))


def downgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('birthday_prompt_snoozed_until')
        batch_op.drop_column('hide_birthday')
        batch_op.drop_column('birthday_month')
        batch_op.drop_column('birthday_day')

    with op.batch_alter_table('zeus_birthday_wishes', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_zeus_birthday_wishes_recipient_id'))
        batch_op.drop_index(batch_op.f('ix_zeus_birthday_wishes_created_at'))

    op.drop_table('zeus_birthday_wishes')
