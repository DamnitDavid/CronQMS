"""add documents.owner_group_id (owning team via assignee_groups)

Revision ID: b8c2d3e4f5a6
Revises: a7b1c2d3e4f5
Create Date: 2026-07-17 00:10:00.000000

Documents gain an optional owning group, reusing the existing
``assignee_groups`` primitive (the same one events/alerts use).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8c2d3e4f5a6'
down_revision: Union[str, None] = 'a7b1c2d3e4f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.add_column(sa.Column("owner_group_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_documents_owner_group_id_assignee_groups",
            "assignee_groups",
            ["owner_group_id"],
            ["id"],
        )
        batch.create_index(
            "ix_documents_owner_group_id", ["owner_group_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.drop_index("ix_documents_owner_group_id")
        batch.drop_constraint(
            "fk_documents_owner_group_id_assignee_groups", type_="foreignkey"
        )
        batch.drop_column("owner_group_id")
