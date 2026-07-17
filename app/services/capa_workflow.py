"""CAPA workflow rules, shared by the JSON API and the browser UI.

Keeping the transition, gate, verification, reopen, and cancel rules here means
every entry point enforces identical guarantees. Functions mutate the passed
CAPA and raise :class:`WorkflowError` on a rule violation; the caller owns the
transaction.
"""

from datetime import date
from typing import Callable, Optional

from sqlalchemy.orm import Session

from app.core.audit import set_audit_reason
from app.models import Capa, User
from app.models.capa import CapaStatus, VerificationOutcome
from app.services.event_workflow import WorkflowError

# Non-terminal transitions. Closing, failing, reopening, and cancelling are
# separate, privileged actions and are intentionally not reachable here.
ALLOWED_TRANSITIONS: dict[CapaStatus, set[CapaStatus]] = {
    CapaStatus.DRAFT: {CapaStatus.INVESTIGATION},
    CapaStatus.INVESTIGATION: {CapaStatus.ACTION_PLAN},
    CapaStatus.ACTION_PLAN: {CapaStatus.IMPLEMENTATION, CapaStatus.INVESTIGATION},
    CapaStatus.IMPLEMENTATION: {CapaStatus.EFFECTIVENESS_CHECK, CapaStatus.ACTION_PLAN},
    CapaStatus.EFFECTIVENESS_CHECK: {CapaStatus.IMPLEMENTATION},
    CapaStatus.CLOSED: set(),
    CapaStatus.FAILED_EFFECTIVENESS: set(),
    CapaStatus.CANCELLED: set(),
}

TERMINAL_STATUSES = {CapaStatus.CLOSED, CapaStatus.FAILED_EFFECTIVENESS, CapaStatus.CANCELLED}
REOPENABLE_STATUSES = {CapaStatus.CLOSED, CapaStatus.FAILED_EFFECTIVENESS}


def _blank(value: Optional[str]) -> bool:
    return not value or not value.strip()


def _gate_draft_to_investigation(capa: Capa) -> Optional[str]:
    if _blank(capa.initiating_cause) and not capa.events:
        return (
            "An initiating cause is required before investigation: set "
            "initiating_cause or link at least one event."
        )
    return None


def _gate_investigation_to_action_plan(capa: Capa) -> Optional[str]:
    if _blank(capa.root_cause):
        return "A root cause is required before moving to the action plan."
    return None


def _gate_action_plan_to_implementation(capa: Capa) -> Optional[str]:
    if _blank(capa.corrective_action) and _blank(capa.preventive_action):
        return "A corrective or preventive action is required before implementation."
    if capa.owner_id is None:
        return "An owner is required before implementation."
    if capa.due_date is None:
        return "A due date is required before implementation."
    return None


def _gate_implementation_to_effectiveness_check(capa: Capa) -> Optional[str]:
    if _blank(capa.corrective_action) and _blank(capa.preventive_action):
        return "A recorded corrective or preventive action is required before the effectiveness check."
    return None


_GATES: dict[tuple[CapaStatus, CapaStatus], Callable[[Capa], Optional[str]]] = {
    (CapaStatus.DRAFT, CapaStatus.INVESTIGATION): _gate_draft_to_investigation,
    (CapaStatus.INVESTIGATION, CapaStatus.ACTION_PLAN): _gate_investigation_to_action_plan,
    (CapaStatus.ACTION_PLAN, CapaStatus.IMPLEMENTATION): _gate_action_plan_to_implementation,
    (CapaStatus.IMPLEMENTATION, CapaStatus.EFFECTIVENESS_CHECK): _gate_implementation_to_effectiveness_check,
}


def apply_status_transition(capa: Capa, new_status: CapaStatus) -> None:
    """Advance a CAPA through a non-terminal transition."""
    current = CapaStatus(capa.status)
    if new_status not in ALLOWED_TRANSITIONS[current]:
        raise WorkflowError(
            f"Invalid status transition from {current.value} to {new_status.value}. "
            "Use verify/reopen/cancel for terminal states.",
            status_code=400,
        )
    gate = _GATES.get((current, new_status))
    if gate is not None:
        error = gate(capa)
        if error is not None:
            raise WorkflowError(error, status_code=400)
    capa.status = new_status.value


def verify_effectiveness(
    db: Session,
    capa: Capa,
    actor: User,
    outcome: VerificationOutcome,
    verification_date: Optional[date],
    reason: Optional[str],
) -> None:
    """Record an independent effectiveness verification of the CAPA.

    Verification must be performed by someone other than the CAPA owner, and
    the CAPA must be in the Effectiveness_Check stage. An Effective outcome
    closes the CAPA; an Ineffective outcome fails it.
    """
    if capa.status != CapaStatus.EFFECTIVENESS_CHECK.value:
        raise WorkflowError(
            "Only a CAPA in Effectiveness_Check can be verified", status_code=400
        )
    if capa.owner_id is not None and capa.owner_id == actor.id:
        raise WorkflowError(
            "Effectiveness verification must be independent of the CAPA owner",
            status_code=403,
        )
    if outcome == VerificationOutcome.PENDING:
        raise WorkflowError("A verification outcome of Pending records nothing", status_code=400)

    set_audit_reason(db, reason)
    capa.verification_outcome = outcome.value
    capa.verification_date = verification_date or date.today()
    capa.verified_by = actor.id
    if outcome == VerificationOutcome.EFFECTIVE:
        capa.status = CapaStatus.CLOSED.value
    elif outcome == VerificationOutcome.INEFFECTIVE:
        capa.status = CapaStatus.FAILED_EFFECTIVENESS.value


def reopen(db: Session, capa: Capa, reason: str) -> None:
    """Reopen a Closed or Failed_Effectiveness CAPA into Investigation."""
    if capa.status not in {s.value for s in REOPENABLE_STATUSES}:
        raise WorkflowError(
            "Only a Closed or Failed_Effectiveness CAPA can be reopened (Cancelled is not reopenable)",
            status_code=400,
        )
    set_audit_reason(db, reason)
    capa.status = CapaStatus.INVESTIGATION.value
    capa.verification_outcome = VerificationOutcome.PENDING.value
    capa.verification_date = None
    capa.verified_by = None


def cancel(db: Session, capa: Capa, reason: str) -> None:
    """Cancel a CAPA from any non-terminal state, recording the reason."""
    if capa.status in {s.value for s in TERMINAL_STATUSES}:
        raise WorkflowError("A terminal CAPA cannot be cancelled", status_code=400)
    set_audit_reason(db, reason)
    capa.status = CapaStatus.CANCELLED.value
