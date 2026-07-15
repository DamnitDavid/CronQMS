"""event closure approval fields

Revision ID: fdffb66d1814
Revises: 90be4ed6ff1f
Create Date: 2026-07-15 07:33:27.803156

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fdffb66d1814'
down_revision: Union[str, None] = '90be4ed6ff1f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Batch mode so the added foreign key works on SQLite as well as Postgres.
    with op.batch_alter_table("events") as batch:
        batch.add_column(sa.Column("closed_by", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("closed_at", sa.DateTime(), nullable=True))
        batch.create_foreign_key("fk_events_closed_by", "users", ["closed_by"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("events") as batch:
        batch.drop_constraint("fk_events_closed_by", type_="foreignkey")
        batch.drop_column("closed_at")
        batch.drop_column("closed_by")
