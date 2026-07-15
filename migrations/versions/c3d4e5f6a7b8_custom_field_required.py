"""required flag for custom fields

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-07-15 15:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Batch mode so the ALTER works on SQLite (tests) as well as Postgres.
    with op.batch_alter_table("custom_fields") as batch:
        batch.add_column(
            sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.false())
        )
    # Drop the server default now that existing rows are backfilled to false.
    with op.batch_alter_table("custom_fields") as batch:
        batch.alter_column("required", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("custom_fields") as batch:
        batch.drop_column("required")
