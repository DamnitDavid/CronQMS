"""Append-only audit trail.

Every field-level mutation to an auditable entity (events, and later CAPAs) is
recorded here: who changed what, when, from what to what, and why. The table is
**append-only** — updates and deletes are rejected at the database level by
triggers (see the DDL below), not merely by ORM convention.
"""

from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    event,
)
from sqlalchemy.schema import DDL

from app.database import Base


class EventHistory(Base):
    """One row per changed field. Immutable once written."""

    __tablename__ = "event_history"

    id = Column(Integer, primary_key=True, index=True)
    entity_type = Column(String(50), nullable=False)
    entity_id = Column(Integer, nullable=False)
    field = Column(String(100), nullable=False)
    old_value = Column(Text, nullable=True)
    new_value = Column(Text, nullable=True)
    reason = Column(Text, nullable=True)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        # "Show the full history of event 4471" must be a single indexed lookup.
        Index("ix_event_history_entity", "entity_type", "entity_id"),
        Index("ix_event_history_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<EventHistory(entity={self.entity_type}:{self.entity_id}, "
            f"field={self.field}, actor={self.actor_id})>"
        )


# --- Database-level append-only enforcement --------------------------------
# These triggers reject any UPDATE or DELETE on event_history. They are the
# authoritative guarantee; the ORM never issues such statements, but the DB
# enforces it regardless of how the table is reached.

SQLITE_APPEND_ONLY_DDL = [
    "CREATE TRIGGER IF NOT EXISTS trg_event_history_no_update "
    "BEFORE UPDATE ON event_history "
    "BEGIN SELECT RAISE(ABORT, 'event_history is append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS trg_event_history_no_delete "
    "BEFORE DELETE ON event_history "
    "BEGIN SELECT RAISE(ABORT, 'event_history is append-only'); END",
]

POSTGRES_APPEND_ONLY_DDL = [
    "CREATE OR REPLACE FUNCTION reject_event_history_mutation() "
    "RETURNS TRIGGER AS $$ BEGIN "
    "RAISE EXCEPTION 'event_history is append-only'; "
    "END; $$ LANGUAGE plpgsql",
    "CREATE TRIGGER trg_event_history_no_update "
    "BEFORE UPDATE ON event_history "
    "FOR EACH ROW EXECUTE FUNCTION reject_event_history_mutation()",
    "CREATE TRIGGER trg_event_history_no_delete "
    "BEFORE DELETE ON event_history "
    "FOR EACH ROW EXECUTE FUNCTION reject_event_history_mutation()",
]

# Attach the triggers whenever the table is created via metadata (e.g. the test
# suite's create_all). Alembic migrations issue the same DDL explicitly, since
# op.create_table does not fire these events.
for _stmt in SQLITE_APPEND_ONLY_DDL:
    event.listen(EventHistory.__table__, "after_create", DDL(_stmt).execute_if(dialect="sqlite"))
for _stmt in POSTGRES_APPEND_ONLY_DDL:
    event.listen(EventHistory.__table__, "after_create", DDL(_stmt).execute_if(dialect="postgresql"))
