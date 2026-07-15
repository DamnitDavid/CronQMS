"""append-only event_history audit trail

Revision ID: 33c7ae2ac472
Revises: 82a2008eedc7
Create Date: 2026-07-15 01:23:16.333951

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.models.event_history import (
    POSTGRES_APPEND_ONLY_DDL,
    SQLITE_APPEND_ONLY_DDL,
)


# revision identifiers, used by Alembic.
revision: str = '33c7ae2ac472'
down_revision: Union[str, None] = '82a2008eedc7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "event_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("field", sa.String(length=100), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_event_history_id"), "event_history", ["id"], unique=False)
    op.create_index("ix_event_history_entity", "event_history", ["entity_type", "entity_id"], unique=False)
    op.create_index("ix_event_history_created_at", "event_history", ["created_at"], unique=False)

    # op.create_table does not fire the metadata after_create events, so the
    # append-only triggers are issued here explicitly, per dialect.
    statements = (
        SQLITE_APPEND_ONLY_DDL
        if op.get_bind().dialect.name == "sqlite"
        else POSTGRES_APPEND_ONLY_DDL
    )
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS trg_event_history_no_update ON event_history")
        op.execute("DROP TRIGGER IF EXISTS trg_event_history_no_delete ON event_history")
        op.execute("DROP FUNCTION IF EXISTS reject_event_history_mutation()")
    # SQLite drops table-bound triggers automatically with the table.
    op.drop_index("ix_event_history_created_at", table_name="event_history")
    op.drop_index("ix_event_history_entity", table_name="event_history")
    op.drop_index(op.f("ix_event_history_id"), table_name="event_history")
    op.drop_table("event_history")
