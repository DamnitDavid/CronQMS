"""Event workflow rules, shared by the JSON API and the browser UI.

Keeping the transition, closure, and reopen rules here means both entry points
enforce identical guarantees (investigation-first flow, independent closure,
reason-logged reopen). Functions mutate the passed event and raise
:class:`WorkflowError` on a rule violation; the caller owns the transaction.
"""

from datetime import datetime

from sqlalchemy.orm import Session

from app.core.audit import set_audit_reason
from app.models import Event, User
from app.models.event import EventStatus

# Non-terminal transitions. Closing and reopening are separate, privileged
# actions and are intentionally not reachable here.
ALLOWED_TRANSITIONS = {
    EventStatus.OPEN: {EventStatus.IN_PROGRESS},
    EventStatus.IN_PROGRESS: {EventStatus.RESOLVED, EventStatus.OPEN},
    EventStatus.RESOLVED: {EventStatus.IN_PROGRESS},
    EventStatus.CLOSED: set(),
}


class WorkflowError(Exception):
    """A workflow rule was violated. ``status_code`` maps to HTTP for the API."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def apply_status_transition(event: Event, new_status: EventStatus) -> None:
    """Advance an event through a non-terminal transition."""
    current = EventStatus(event.status)
    if new_status not in ALLOWED_TRANSITIONS[current]:
        raise WorkflowError(
            f"Invalid status transition from {current.value} to {new_status.value}. "
            "Use close/reopen for closure.",
            status_code=400,
        )
    event.status = new_status.value


def approve_closure(event: Event, actor: User) -> None:
    """Close a resolved event via an independent approver."""
    if event.status != EventStatus.RESOLVED.value:
        raise WorkflowError("Only a Resolved event can be closed", status_code=400)
    if actor.id in (event.reported_by, event.assigned_to):
        raise WorkflowError(
            "Closure must be approved by someone other than the reporter or investigator",
            status_code=403,
        )
    event.status = EventStatus.CLOSED.value
    event.closed_by = actor.id
    event.closed_at = datetime.utcnow()


def reopen(db: Session, event: Event, reason: str) -> None:
    """Reopen a closed event, recording the reason in the audit trail."""
    if event.status != EventStatus.CLOSED.value:
        raise WorkflowError("Only a Closed event can be reopened", status_code=400)
    set_audit_reason(db, reason)
    event.status = EventStatus.OPEN.value
    event.closed_by = None
    event.closed_at = None
