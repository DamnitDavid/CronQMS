"""Controlled-document workflow rules, shared by the JSON API and the browser UI.

Keeping the two-stage review/approval rules here means both entry points enforce
identical guarantees: an author submits a Draft for review; an independent
reviewer signs it off; a separate approver approves it into effect. Full
segregation of duties is enforced — author, reviewer, and approver must be three
distinct people — mirroring the independent-closure rule in
``app/services/event_workflow.py``.

Functions mutate the passed objects and raise :class:`WorkflowError` on a rule
violation; the caller owns the transaction. Reason-bearing actions record the
reason in the audit trail via ``set_audit_reason`` first.
"""

import calendar
from datetime import date, datetime

from sqlalchemy.orm import Session

from app.core.audit import set_audit_reason
from app.models import Document, DocumentVersion, User
from app.models.document import DocumentVersionStatus


def add_months(start: date, months: int) -> date:
    """Return ``start`` advanced by ``months`` calendar months.

    The day is clamped to the last valid day of the target month (so
    31 Jan + 1 month is 28/29 Feb), avoiding external date libraries.
    """
    zero_based = start.month - 1 + months
    year = start.year + zero_based // 12
    month = zero_based % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)

# The only non-privileged transition; review/approve/reject/obsolete are
# privileged verbs with their own segregation checks, like event close/reopen.
ALLOWED_TRANSITIONS = {
    DocumentVersionStatus.DRAFT: {DocumentVersionStatus.IN_REVIEW},
    DocumentVersionStatus.IN_REVIEW: set(),
    DocumentVersionStatus.PENDING_APPROVAL: set(),
    DocumentVersionStatus.EFFECTIVE: set(),
    DocumentVersionStatus.OBSOLETE: set(),
    DocumentVersionStatus.REJECTED: set(),
}


class WorkflowError(Exception):
    """A workflow rule was violated. ``status_code`` maps to HTTP for the API."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def submit_for_review(version: DocumentVersion) -> None:
    """Advance a Draft to In Review (author action)."""
    current = DocumentVersionStatus(version.status)
    if DocumentVersionStatus.IN_REVIEW not in ALLOWED_TRANSITIONS[current]:
        raise WorkflowError(
            f"Only a Draft version can be submitted for review (current: {current.value})",
            status_code=400,
        )
    version.status = DocumentVersionStatus.IN_REVIEW.value


def sign_off_review(version: DocumentVersion, actor: User) -> None:
    """Reviewer sign-off: In Review -> Pending Approval.

    The reviewer must be someone other than the author (segregation of duties).
    """
    if version.status != DocumentVersionStatus.IN_REVIEW.value:
        raise WorkflowError("Only a version In Review can be reviewed", status_code=400)
    if actor.id == version.author_id:
        raise WorkflowError(
            "The reviewer must be someone other than the document's author",
            status_code=403,
        )
    version.status = DocumentVersionStatus.PENDING_APPROVAL.value
    version.reviewed_by = actor.id
    version.reviewed_at = datetime.utcnow()


def approve(db: Session, document: Document, version: DocumentVersion, actor: User) -> None:
    """Approver approval: Pending Approval -> Effective.

    The approver must differ from both the author and the reviewer. Approving a
    revision supersedes the currently effective version (marked Obsolete) so that
    exactly one version is effective at a time, and schedules the next periodic
    review from the document's review period.
    """
    if version.status != DocumentVersionStatus.PENDING_APPROVAL.value:
        raise WorkflowError("Only a version Pending Approval can be approved", status_code=400)
    if actor.id in (version.author_id, version.reviewed_by):
        raise WorkflowError(
            "The approver must be someone other than the author and the reviewer",
            status_code=403,
        )

    # Supersede the prior effective version, if any.
    for other in document.versions:
        if other.id != version.id and other.status == DocumentVersionStatus.EFFECTIVE.value:
            other.status = DocumentVersionStatus.OBSOLETE.value

    today = date.today()
    version.status = DocumentVersionStatus.EFFECTIVE.value
    version.approved_by = actor.id
    version.approved_at = datetime.utcnow()
    version.effective_date = today

    if document.review_period_months:
        document.next_review_date = add_months(today, document.review_period_months)
    else:
        document.next_review_date = None
    # A fresh approval clears any prior retention clock; it is set again on
    # obsolescence.
    document.retention_until = None


def reject(db: Session, version: DocumentVersion, actor: User, reason: str) -> None:
    """Send a version under review/approval back to Draft, with a logged reason.

    Whoever rejects must not be the author, matching the segregation of duties
    on the sign-off they are declining.
    """
    if version.status not in (
        DocumentVersionStatus.IN_REVIEW.value,
        DocumentVersionStatus.PENDING_APPROVAL.value,
    ):
        raise WorkflowError(
            "Only a version In Review or Pending Approval can be rejected", status_code=400
        )
    if actor.id == version.author_id:
        raise WorkflowError(
            "A version must be rejected by someone other than its author", status_code=403
        )
    set_audit_reason(db, reason)
    version.status = DocumentVersionStatus.DRAFT.value
    version.reviewed_by = None
    version.reviewed_at = None
    version.approved_by = None
    version.approved_at = None


def obsolete(db: Session, document: Document, version: DocumentVersion, reason: str) -> None:
    """Manually retire the effective version, starting the retention clock."""
    if version.status != DocumentVersionStatus.EFFECTIVE.value:
        raise WorkflowError("Only the Effective version can be obsoleted", status_code=400)
    set_audit_reason(db, reason)
    version.status = DocumentVersionStatus.OBSOLETE.value
    document.next_review_date = None
    if document.retention_period_months:
        document.retention_until = add_months(date.today(), document.retention_period_months)
    else:
        document.retention_until = None
