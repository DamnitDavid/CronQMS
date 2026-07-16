"""Training certification rules, shared by the JSON API and the browser UI.

Keeping the certify-and-expiry rule in one place means both entry points record
identical evidence: the trainer who signed off, the date, the typed trainee
acknowledgment, and — when the course requires periodic recertification — the
computed expiry date. Mirrors ``app/services/document_workflow.py``.
"""

from datetime import date
from typing import Optional

from app.models import TrainingCourse, TrainingRecord, TrainingStatus
from app.services.document_workflow import add_months


def certify_record(
    record: TrainingRecord,
    course: TrainingCourse,
    trainer_id: int,
    acknowledgment: str,
    trained_date: Optional[date] = None,
) -> None:
    """Mark ``record`` trained and compute its expiry from the course policy.

    Mutates the passed record; the caller owns the transaction. ``trained_date``
    defaults to today. Expiry is set only when the course defines a
    recertification period, and is cleared otherwise (so re-certifying a course
    that dropped its recert period clears a stale expiry).
    """
    when = trained_date or date.today()
    record.status = TrainingStatus.TRAINED.value
    record.trained_date = when
    record.trained_by = trainer_id
    record.trainee_acknowledgment = acknowledgment
    if course.recertification_period_months:
        record.expiry_date = add_months(when, course.recertification_period_months)
    else:
        record.expiry_date = None
