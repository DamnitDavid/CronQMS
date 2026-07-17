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

    Returns ``[{"title", "modules": [{"key", "label", "href"}, ...]}, ...]`` with
    modules the caller lacks privilege for removed and empty groups dropped.
    """
    granted_set = set(granted or ())
    result: list[dict] = []
    for group in layout.get("groups", []):
        items = []
        for key in group.get("modules", []):
            mod = MODULES.get(key)
            if mod and mod["permission"] in granted_set:
                items.append({"key": key, "label": mod["label"], "href": mod["href"]})
        if items:
            result.append({"title": group["title"], "modules": items})
    return result


def visible_nav(db: Session, user) -> list[dict]:
    """Build the sidebar for ``user`` from their org layout and granted perms."""
    granted = getattr(user, "granted_permissions", set())
    layout = get_layout(db, user.organization_id) if user.organization_id else DEFAULT_LAYOUT
    return build_nav(layout, granted)
