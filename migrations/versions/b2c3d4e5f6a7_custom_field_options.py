"""dropdown options for custom fields

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-07-15 14:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Batch mode so the ALTER works on SQLite (tests) as well as Postgres.
    with op.batch_alter_table("custom_fields") as batch:
        batch.add_column(sa.Column("options", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("custom_fields") as batch:
        batch.drop_column("options")
