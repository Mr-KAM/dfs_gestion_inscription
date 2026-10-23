"""exercises: history of every answer, permission to resubmit, images of the rich editor

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-23 16:00:00

"""
import sqlalchemy as sa
from alembic import op

revision = '0019'
down_revision = '0018'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('exercise_submissions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('resubmit_allowed', sa.Boolean(), server_default=sa.false(), nullable=False))
    op.create_table(
        'exercise_answers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('submission_id', sa.Integer(), nullable=False),
        sa.Column('number', sa.Integer(), nullable=False),
        sa.Column('answer', sa.Text(), nullable=False),
        sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('score', sa.String(length=20), nullable=True),
        sa.Column('feedback', sa.Text(), nullable=True),
        sa.Column('reviewed_by', sa.Integer(), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['submission_id'], ['exercise_submissions.id'],
                                name=op.f('fk_exercise_answers_submission_id_exercise_submissions'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['reviewed_by'], ['users.id'], name=op.f('fk_exercise_answers_reviewed_by_users'),
                                ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_exercise_answers')),
        sa.UniqueConstraint('submission_id', 'number', name='uq_exercise_answers_submission_number'),
    )
    op.create_index(op.f('ix_exercise_answers_submission_id'), 'exercise_answers', ['submission_id'], unique=False)
    # The answer already handed in becomes submission no. 1 of the history.
    op.execute("INSERT INTO exercise_answers (submission_id, number, answer, submitted_at, status, score, feedback, "
               "reviewed_by, reviewed_at) SELECT id, 1, answer, submitted_at, status, score, feedback, reviewed_by, "
               "reviewed_at FROM exercise_submissions WHERE submitted_at IS NOT NULL")
    op.create_table(
        'media',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('key', sa.String(length=32), nullable=False),
        sa.Column('data', sa.LargeBinary(), nullable=False),
        sa.Column('mimetype', sa.String(length=20), nullable=False),
        sa.Column('size', sa.Integer(), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_media_created_by_users'), ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_media')),
        sa.UniqueConstraint('key', name=op.f('uq_media_key')),
    )


def downgrade():
    op.drop_table('media')
    op.drop_index(op.f('ix_exercise_answers_submission_id'), table_name='exercise_answers')
    op.drop_table('exercise_answers')
    with op.batch_alter_table('exercise_submissions', schema=None) as batch_op:
        batch_op.drop_column('resubmit_allowed')
