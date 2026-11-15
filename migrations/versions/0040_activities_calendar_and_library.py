"""activities calendar and library

Revision ID: 0040
Revises: 0039
Create Date: 2026-11-15 15:30:47.195298

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0040'
down_revision = '0039'
branch_labels = None
depends_on = None


def upgrade():

    op.create_table('books',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('author', sa.String(length=200), nullable=False),
    sa.Column('category', sa.String(length=100), nullable=False),
    sa.Column('reference', sa.String(length=50), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('url', sa.String(length=500), nullable=False),
    sa.Column('copies', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("kind IN ('digital', 'physical')", name=op.f('ck_books_kind')),
    sa.CheckConstraint('copies >= 0', name=op.f('ck_books_copies')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_books'))
    )
    op.create_table('activities',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=150), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('place', sa.String(length=150), nullable=False),
    sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ends_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('everyone', sa.Boolean(), server_default=sa.false(), nullable=False),
    sa.Column('roles', sa.JSON(), server_default='[]', nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('ends_at > starts_at', name=op.f('ck_activities_ends_after_start')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_activities_created_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_activities'))
    )
    with op.batch_alter_table('activities', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_activities_starts_at'), ['starts_at'], unique=False)

    op.create_table('book_loans',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('book_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('requested_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('decided_by', sa.Integer(), nullable=True),
    sa.Column('borrowed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('due_on', sa.Date(), nullable=True),
    sa.Column('returned_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('note', sa.String(length=300), nullable=False),
    sa.CheckConstraint("status IN ('requested', 'refused', 'borrowed', 'returned')", name=op.f('ck_book_loans_status')),
    sa.ForeignKeyConstraint(['book_id'], ['books.id'], name=op.f('fk_book_loans_book_id_books'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['decided_by'], ['users.id'], name=op.f('fk_book_loans_decided_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_book_loans_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_book_loans'))
    )
    with op.batch_alter_table('book_loans', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_book_loans_book_id'), ['book_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_book_loans_user_id'), ['user_id'], unique=False)

    op.create_table('activity_participants',
    sa.Column('activity_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['activity_id'], ['activities.id'], name=op.f('fk_activity_participants_activity_id_activities'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_activity_participants_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('activity_id', 'user_id', name=op.f('pk_activity_participants'))
    )



def downgrade():

    op.drop_table('activity_participants')
    with op.batch_alter_table('book_loans', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_book_loans_user_id'))
        batch_op.drop_index(batch_op.f('ix_book_loans_book_id'))

    op.drop_table('book_loans')
    with op.batch_alter_table('activities', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_activities_starts_at'))

    op.drop_table('activities')
    op.drop_table('books')

