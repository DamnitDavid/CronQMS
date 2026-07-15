"""The single audit choke point.

Auditable models opt in via :func:`register_auditing`, which attaches
mapper-level ``after_insert`` / ``after_update`` / ``after_delete`` listeners.
Those listeners write :class:`~app.models.event_history.EventHistory` rows on
the *same* connection as the change, so the audit record commits atomically
with the mutation it describes. No route handler writes history directly.

Actor and reason are request context, unknown to the ORM, so handlers deposit
them on ``session.info`` (via :func:`set_audit_actor` / :func:`set_audit_reason`)
and the listeners read them back at flush time.
"""

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import inspect
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapper, Session, object_session

from app.models.event_history import EventHistory

_ACTOR_KEY = "audit_actor_id"
_REASON_KEY = "audit_reason"


def set_audit_actor(session: Session, actor_id: Optional[int]) -> None:
    """Record who is responsible for subsequent mutations on this session."""
    session.info[_ACTOR_KEY] = actor_id


def set_audit_reason(session: Session, reason: Optional[str]) -> None:
    """Attach a reason-for-change to subsequent mutations on this session."""
    session.info[_REASON_KEY] = reason


def _stringify(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(value)


def _context(target: Any) -> tuple[Optional[int], Optional[str]]:
    session = object_session(target)
    if session is None:
        return None, None
    return session.info.get(_ACTOR_KEY), session.info.get(_REASON_KEY)


def _write(
    connection: Connection,
    target: Any,
    field: str,
    old: Any,
    new: Any,
    actor_id: Optional[int],
    reason: Optional[str],
) -> None:
    connection.execute(
        EventHistory.__table__.insert().values(
            entity_type=target.__audit_entity__,
            entity_id=target.id,
            field=field,
            old_value=_stringify(old),
            new_value=_stringify(new),
            reason=reason,
            actor_id=actor_id,
            created_at=datetime.utcnow(),
        )
    )


def _after_insert(mapper: Mapper, connection: Connection, target: Any) -> None:
    actor_id, reason = _context(target)
    for field in target.__audit_fields__:
        value = getattr(target, field)
        if value is None:
            continue
        _write(connection, target, field, None, value, actor_id, reason)


def _after_update(mapper: Mapper, connection: Connection, target: Any) -> None:
    actor_id, reason = _context(target)
    state = inspect(target)
    for field in target.__audit_fields__:
        history = state.attrs[field].history
        if not history.has_changes():
            continue
        old = history.deleted[0] if history.deleted else None
        new = history.added[0] if history.added else None
        _write(connection, target, field, old, new, actor_id, reason)


def _after_delete(mapper: Mapper, connection: Connection, target: Any) -> None:
    # Auditable entities are soft-deleted (is_active=False), captured as an
    # update. A hard delete still leaves a tombstone here for completeness.
    actor_id, reason = _context(target)
    _write(connection, target, "__deleted__", None, None, actor_id, reason)


def register_auditing(model: type) -> None:
    """Enable audit capture for ``model``.

    ``model`` must define ``__audit_entity__`` (a short entity-type string) and
    ``__audit_fields__`` (the columns to track).
    """
    from sqlalchemy import event

    event.listen(model, "after_insert", _after_insert)
    event.listen(model, "after_update", _after_update)
    event.listen(model, "after_delete", _after_delete)
