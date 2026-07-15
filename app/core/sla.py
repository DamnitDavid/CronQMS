"""Service-level targets derived from event priority.

The target close date for an event is derived from its priority unless one is
supplied explicitly. Calendar days are used (not business days) for simplicity;
swap the mapping here to retune SLAs.
"""

from datetime import date, timedelta

from app.models.event import EventPriority

SLA_DAYS: dict[EventPriority, int] = {
    EventPriority.CRITICAL: 7,
    EventPriority.HIGH: 14,
    EventPriority.MEDIUM: 30,
    EventPriority.LOW: 60,
}


def sla_days(priority_value: str) -> int:
    """Return the SLA window in days for a priority value."""
    return SLA_DAYS.get(EventPriority(priority_value), 30)


def sla_target(priority_value: str, start: date) -> date:
    """Return the target close date for an event of the given priority."""
    return start + timedelta(days=sla_days(priority_value))
