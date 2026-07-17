"""Org-admin-configurable sidebar navigation.

An organization admin defines *nav-groups* and places module "flex containers"
into them. The layout is stored per-organization as JSON in the generic
``OrgSetting`` key/value store (key ``nav.layout``) — no dedicated table needed.

Each placeable module maps to a required permission (its existing per-module
read permission). ``visible_nav`` intersects the configured layout with the
caller's granted permissions, so a module a Role lacks privilege for is simply
absent from that user's sidebar — the "no privilege ⇒ container hidden" rule.
"""

import json
from typing import Iterable

from sqlalchemy.orm import Session

from app.models.alert import Alert, AlertStatus
from app.models.capa import Capa, CapaStatus
from app.models.event import Event, EventStatus
from app.services import org_settings

# OrgSetting key holding the JSON nav layout.
KEY_NAV_LAYOUT = "nav.layout"


# Canonical registry of placeable modules ("flex containers"). ``key`` doubles
# as the ``active_nav`` value each page sets, so the current item highlights.
MODULES: dict[str, dict] = {
    "events": {"label": "Events", "href": "/admin/events", "permission": "event:read"},
    "alerts": {"label": "Alerts", "href": "/admin/alerts", "permission": "alert:read"},
    "audit-log": {"label": "Audit Log", "href": "/admin/audit-log", "permission": "audit_log:view"},
    "capa": {"label": "CAPA", "href": "/admin/capa", "permission": "capa:read"},
    "documents": {"label": "Documents", "href": "/admin/documents", "permission": "document:read"},
    "audits": {"label": "Audits", "href": "/admin/audits", "permission": "audit:read"},
    "training": {"label": "Training", "href": "/admin/training", "permission": "training:read"},
    "changes": {"label": "Change Control", "href": "/admin/changes", "permission": "change:read"},
    "reports": {"label": "Reports", "href": "/admin/reports", "permission": "dashboard:view"},
    "users": {"label": "Users", "href": "/admin/users", "permission": "user:manage"},
    "settings": {"label": "Settings", "href": "/admin/settings/custom-fields", "permission": "settings:manage"},
}

# Nav module keys that show a live open-item count badge, and the statuses
# that count as "open" for each. Modules not listed here get no badge.
_OPEN_EVENT_STATUSES = (EventStatus.OPEN.value, EventStatus.IN_PROGRESS.value)
_OPEN_CAPA_STATUSES = (
    CapaStatus.OPEN.value,
    CapaStatus.IN_PROGRESS.value,
    CapaStatus.PENDING_VERIFICATION.value,
)
_OPEN_ALERT_STATUSES = (AlertStatus.OPEN.value, AlertStatus.ACKNOWLEDGED.value)

# The default sidebar: mirrors the historical layout (Dashboard removed).
DEFAULT_LAYOUT: dict = {
    "groups": [
        {"title": "Operations", "modules": ["events", "alerts", "audit-log"]},
        {
            "title": "Management",
            "modules": [
                "capa",
                "documents",
                "audits",
                "training",
                "changes",
                "reports",
                "users",
                "settings",
            ],
        },
    ]
}


def _sanitize(layout: dict) -> dict:
    """Coerce a parsed layout into the expected shape, dropping unknown keys.

    Keeps only recognized module keys (in their given order) and requires each
    group to have a title; malformed input degrades gracefully rather than
    breaking the shell for everyone.
    """
    groups = []
    for group in (layout or {}).get("groups", []):
        if not isinstance(group, dict):
            continue
        title = str(group.get("title", "")).strip()
        modules = [m for m in group.get("modules", []) if m in MODULES]
        if title:
            groups.append({"title": title, "modules": modules})
    return {"groups": groups} if groups else DEFAULT_LAYOUT


def get_layout(db: Session, organization_id: int) -> dict:
    """Return the org's saved nav layout, or the default if unset/invalid."""
    raw = org_settings.get_setting(db, organization_id, KEY_NAV_LAYOUT)
    if not raw:
        return DEFAULT_LAYOUT
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return DEFAULT_LAYOUT
    return _sanitize(parsed)


def set_layout(db: Session, organization_id: int, layout: dict) -> None:
    """Persist the org's nav layout (sanitized). Does not commit."""
    org_settings.set_setting(
        db, organization_id, KEY_NAV_LAYOUT, json.dumps(_sanitize(layout))
    )


def build_nav(layout: dict, granted: Iterable[str]) -> list[dict]:
    """Resolve a layout + permission set into renderable groups.

    Returns ``[{"title", "modules": [{"key", "label", "href", "count"}, ...]}, ...]``
    with modules the caller lacks privilege for removed and empty groups dropped.
    ``count`` is ``None`` until ``visible_nav`` fills in live badge counts.
    """
    granted_set = set(granted or ())
    result: list[dict] = []
    for group in layout.get("groups", []):
        items = []
        for key in group.get("modules", []):
            mod = MODULES.get(key)
            if mod and mod["permission"] in granted_set:
                items.append({"key": key, "label": mod["label"], "href": mod["href"], "count": None})
        if items:
            result.append({"title": group["title"], "modules": items})
    return result


def _nav_counts(db: Session, organization_id: int, keys: set[str]) -> dict[str, int]:
    """Open-item counts for the nav badge, one targeted query per requested key."""
    counts: dict[str, int] = {}
    if "events" in keys:
        counts["events"] = (
            db.query(Event)
            .filter(Event.organization_id == organization_id, Event.status.in_(_OPEN_EVENT_STATUSES))
            .count()
        )
    if "capa" in keys:
        counts["capa"] = (
            db.query(Capa)
            .filter(Capa.organization_id == organization_id, Capa.status.in_(_OPEN_CAPA_STATUSES))
            .count()
        )
    if "alerts" in keys:
        counts["alerts"] = (
            db.query(Alert)
            .filter(Alert.organization_id == organization_id, Alert.status.in_(_OPEN_ALERT_STATUSES))
            .count()
        )
    return counts


def visible_nav(db: Session, user) -> list[dict]:
    """Build the sidebar for ``user`` from their org layout and granted perms."""
    granted = getattr(user, "granted_permissions", set())
    layout = get_layout(db, user.organization_id) if user.organization_id else DEFAULT_LAYOUT
    nav = build_nav(layout, granted)

    if user.organization_id:
        keys = {m["key"] for g in nav for m in g["modules"]} & {"events", "capa", "alerts"}
        if keys:
            counts = _nav_counts(db, user.organization_id, keys)
            for group in nav:
                for module in group["modules"]:
                    if module["key"] in counts:
                        module["count"] = counts[module["key"]]
    return nav
