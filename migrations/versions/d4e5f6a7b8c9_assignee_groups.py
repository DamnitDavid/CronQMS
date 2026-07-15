"""assignee groups and event.assigned_group_id

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-07-15 15:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'assignee_groups',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_assignee_groups_id'), 'assignee_groups', ['id'], unique=False)
    op.create_index(op.f('ix_assignee_groups_organization_id'), 'assignee_groups', ['organization_id'], unique=False)

    op.create_table(
        'assignee_group_members',
        sa.Column('group_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['group_id'], ['assignee_groups.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('group_id', 'user_id'),
    )

    with op.batch_alter_table("events") as batch:
        batch.add_column(sa.Column("assigned_group_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_events_assigned_group_id", "assignee_groups", ["assigned_group_id"], ["id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("events") as batch:
        batch.drop_constraint("fk_events_assigned_group_id", type_="foreignkey")
        batch.drop_column("assigned_group_id")
    op.drop_table('assignee_group_members')
    op.drop_index(op.f('ix_assignee_groups_organization_id'), table_name='assignee_groups')
    op.drop_index(op.f('ix_assignee_groups_id'), table_name='assignee_groups')
    op.drop_table('assignee_groups')
