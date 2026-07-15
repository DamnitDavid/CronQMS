"""Helpers for admin-defined custom fields on events.

Keeps the route handlers thin: definition lookup, value lookup, value
persistence with per-type coercion/validation, and display formatting.
"""

import re

from sqlalchemy.orm import Session

from app.models.custom_field import CustomField, CustomFieldType, EventCustomValue


def fields_for(db: Session, organization_id: int, event_type: str) -> list[CustomField]:
    """Active custom-field definitions for an org + event type, in form order."""
    return (
        db.query(CustomField)
        .filter(
            CustomField.organization_id == organization_id,
            CustomField.event_type == event_type,
            CustomField.is_active.is_(True),
        )
        .order_by(CustomField.display_order.asc(), CustomField.id.asc())
        .all()
    )


def values_for(db: Session, event_id: int) -> dict[int, str]:
    """Map of ``{custom_field_id: value}`` for an event."""
    rows = (
        db.query(EventCustomValue)
        .filter(EventCustomValue.event_id == event_id)
        .all()
    )
    return {row.custom_field_id: row.value for row in rows}


def slugify_key(label: str) -> str:
    """Derive a stable snake_case key from a human label."""
    slug = re.sub(r"[^a-z0-9]+", "_", label.strip().lower()).strip("_")
    return slug[:100] or "field"


def unique_key(db: Session, organization_id: int, event_type: str, label: str) -> str:
    """A key derived from ``label``, unique per (organization, event_type)."""
    base = slugify_key(label)
    existing = {
        row.key
        for row in db.query(CustomField.key).filter(
            CustomField.organization_id == organization_id,
            CustomField.event_type == event_type,
        )
    }
    if base not in existing:
        return base
    suffix = 2
    while f"{base}_{suffix}" in existing:
        suffix += 1
    return f"{base}_{suffix}"


def _coerce(field: CustomField, raw) -> tuple[str | None, str | None]:
    """Coerce a submitted raw value to stored text, or return an error message.

    Returns ``(stored_value_or_None, error_or_None)``. ``None`` stored value
    means "no value" (the row is deleted).
    """
    if field.field_type == CustomFieldType.BOOLEAN.value:
        # Checkbox: present (any truthy string) -> true, absent -> false.
        checked = raw not in (None, "", "false", "off", "0")
        return ("true" if checked else "false"), None

    text = (raw or "").strip() if isinstance(raw, str) else ""
    if text == "":
        return None, None

    if field.field_type == CustomFieldType.NUMBER.value:
        try:
            float(text)
        except ValueError:
            return None, f"{field.label} must be a number."
        return text, None

    return text, None


def save_values(db: Session, event, fields: list[CustomField], form) -> str | None:
    """Persist custom-field values for ``event`` from a submitted form multidict.

    Reads ``cf_<field_id>`` keys. Upserts a row per field with a value, deletes
    rows whose value is cleared. Does not commit — the caller owns the
    transaction. Returns an error message on the first invalid value, else None.
    """
    existing = {
        row.custom_field_id: row
        for row in db.query(EventCustomValue).filter(
            EventCustomValue.event_id == event.id
        )
    }
    for field in fields:
        raw = form.get(f"cf_{field.id}")
        stored, error = _coerce(field, raw)
        if error:
            return error
        row = existing.get(field.id)
        if stored is None:
            if row is not None:
                db.delete(row)
        elif row is not None:
            row.value = stored
        else:
            db.add(EventCustomValue(event_id=event.id, custom_field_id=field.id, value=stored))
    return None


def display_value(field: CustomField, raw: str | None) -> str:
    """Human-friendly rendering of a stored value for the detail view."""
    if field.field_type == CustomFieldType.BOOLEAN.value:
        return "Yes" if raw == "true" else "No"
    if raw in (None, ""):
        return "—"
    return raw
