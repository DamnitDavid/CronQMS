"""Typed access to per-organization configuration (``OrgSetting`` key/value rows).

Admin-managed toggles live here so new options don't each need a schema change.
Values are stored as strings and coerced by the accessors below.
"""

from sqlalchemy.orm import Session

from app.models import OrgSetting

# Keys.
KEY_ALLOW_STANDALONE = "alerts.allow_standalone"
KEY_DEFAULT_EXPIRY_DAYS = "alerts.default_expiry_days"

DEFAULT_EXPIRY_DAYS = 14


def get_setting(db: Session, organization_id: int, key: str, default=None):
    """Return the raw string value for ``key``, or ``default`` if unset."""
    row = (
        db.query(OrgSetting)
        .filter(OrgSetting.organization_id == organization_id, OrgSetting.key == key)
        .first()
    )
    return row.value if row is not None else default


def set_setting(db: Session, organization_id: int, key: str, value: str) -> None:
    """Upsert the string value for ``key`` (does not commit)."""
    row = (
        db.query(OrgSetting)
        .filter(OrgSetting.organization_id == organization_id, OrgSetting.key == key)
        .first()
    )
    if row is None:
        row = OrgSetting(organization_id=organization_id, key=key, value=value)
        db.add(row)
    else:
        row.value = value


def standalone_alerts_enabled(db: Session, organization_id: int) -> bool:
    """Whether alerts may be created without a CAPA/source event. Default False."""
    return get_setting(db, organization_id, KEY_ALLOW_STANDALONE, "false") == "true"


def default_expiry_days(db: Session, organization_id: int) -> int:
    """The org's default alert expiry in days. Default 14."""
    raw = get_setting(db, organization_id, KEY_DEFAULT_EXPIRY_DAYS, str(DEFAULT_EXPIRY_DAYS))
    try:
        days = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_EXPIRY_DAYS
    return days if days > 0 else DEFAULT_EXPIRY_DAYS
