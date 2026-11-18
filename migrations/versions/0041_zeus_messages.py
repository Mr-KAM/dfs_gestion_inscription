"""zeus class messaging: conversations (student x class), messages, read markers

Revision ID: 0041
Revises: 0040
Create Date: 2026-11-18 12:44:27.980239

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0041'
down_revision = '0040'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('zeus_conversations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('class_id', sa.Integer(), nullable=False),
    sa.Column('enrollment_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_message_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('awaiting_reply', sa.Boolean(), server_default=sa.false(), nullable=False),
    sa.ForeignKeyConstraint(['class_id'], ['zeus_classes.id'], name=op.f('fk_zeus_conversations_class_id_zeus_classes'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['enrollment_id'], ['cohort_enrollments.id'], name=op.f('fk_zeus_conversations_enrollment_id_cohort_enrollments'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_zeus_conversations')),
    sa.UniqueConstraint('class_id', 'enrollment_id', name='uq_zeus_conversations_class_enrollment')
    )
    with op.batch_alter_table('zeus_conversations', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_zeus_conversations_class_id'), ['class_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_zeus_conversations_enrollment_id'), ['enrollment_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_zeus_conversations_last_message_at'), ['last_message_at'], unique=False)

    op.create_table('zeus_conversation_reads',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('conversation_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('last_read_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['zeus_conversations.id'], name=op.f('fk_zeus_conversation_reads_conversation_id_zeus_conversations'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_zeus_conversation_reads_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_zeus_conversation_reads')),
    sa.UniqueConstraint('conversation_id', 'user_id', name='uq_zeus_conversation_reads_conversation_user')
    )
    with op.batch_alter_table('zeus_conversation_reads', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_zeus_conversation_reads_user_id'), ['user_id'], unique=False)

    op.create_table('zeus_messages',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('conversation_id', sa.Integer(), nullable=False),
    sa.Column('author_id', sa.Integer(), nullable=True),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['author_id'], ['users.id'], name=op.f('fk_zeus_messages_author_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['conversation_id'], ['zeus_conversations.id'], name=op.f('fk_zeus_messages_conversation_id_zeus_conversations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_zeus_messages'))
    )
    with op.batch_alter_table('zeus_messages', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_zeus_messages_conversation_id'), ['conversation_id'], unique=False)



def downgrade():
    with op.batch_alter_table('zeus_messages', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_zeus_messages_conversation_id'))

    op.drop_table('zeus_messages')
    with op.batch_alter_table('zeus_conversation_reads', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_zeus_conversation_reads_user_id'))

    op.drop_table('zeus_conversation_reads')
    with op.batch_alter_table('zeus_conversations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_zeus_conversations_last_message_at'))
        batch_op.drop_index(batch_op.f('ix_zeus_conversations_enrollment_id'))
        batch_op.drop_index(batch_op.f('ix_zeus_conversations_class_id'))

    op.drop_table('zeus_conversations')
