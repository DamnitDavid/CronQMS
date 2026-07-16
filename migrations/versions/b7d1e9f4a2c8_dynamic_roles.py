"""dynamic admin-managed roles (roles + role_permissions) with backfill

Revision ID: b7d1e9f4a2c8
Revises: f6a7b8c9d0e1
Create Date: 2026-07-16 12:00:00.000000

Creates the ``roles`` and ``role_permissions`` tables and backfills them so no
existing user loses access:

* every organization gets seeded ``Admin`` (all permissions) and ``User``
  (basic default) system roles, and
* each distinct legacy ``users.role`` in an org other than Admin/User
  (QualityManager, Investigator, Approver, Viewer) becomes an editable custom
  role carrying its previous static grant.

Permission strings are embedded as literals on purpose — migrations must not
import application enums, which may drift from the schema over time.

Note: users whose ``organization_id`` is NULL cannot be mapped to an org-scoped
role and are left as-is; the runtime resolver fails them closed.
"""
from datetime import datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7d1e9f4a2c8'
down_revision: Union[str, None] = 'f6a7b8c9d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# --- Permission catalog + legacy grants (embedded literals) ----------------
ALL_PERMISSIONS = [
    "event:create", "event:read", "event:update", "event:delete",
    "event:change_status", "event:approve_closure", "event:reopen",
    "event:comment", "capa:create", "capa:read", "capa:update", "capa:verify",
    "alert:create", "alert:read", "alert:acknowledge", "alert:close",
    "user:manage", "settings:manage", "dashboard:view",
]

DEFAULT_USER_PERMISSIONS = ["dashboard:view", "event:read", "capa:read", "alert:read"]

# Legacy Role -> granted permissions (mirrors the historical ROLE_PERMISSIONS).
LEGACY_GRANTS = {
    "QualityManager": [
        "event:create", "event:read", "event:update", "event:delete",
        "event:change_status", "event:approve_closure", "event:reopen",
        "event:comment", "capa:create", "capa:read", "capa:update",
        "capa:verify", "alert:create", "alert:read", "alert:acknowledge",
        "alert:close", "dashboard:view",
    ],
    "Investigator": [
        "event:create", "event:read", "event:update", "event:change_status",
        "event:comment", "capa:create", "capa:read", "capa:update",
        "alert:create", "alert:read", "alert:acknowledge", "dashboard:view",
    ],
    "Approver": [
        "event:read", "event:change_status", "event:approve_closure",
        "event:comment", "capa:read", "capa:verify", "alert:read",
        "alert:acknowledge", "dashboard:view",
    ],
    "Viewer": ["event:read", "capa:read", "alert:read", "dashboard:view"],
}

SYSTEM_ROLE_NAMES = {"Admin", "User"}


def _insert_role(bind, org_id, name, description, is_system, permissions):
    """Insert a role and its permission rows; skip if it already exists."""
    exists = bind.execute(
        sa.text("SELECT id FROM roles WHERE organization_id = :o AND name = :n"),
        {"o": org_id, "n": name},
    ).scalar()
    if exists is not None:
        return
    now = datetime.utcnow()
    bind.execute(
        sa.text(
            "INSERT INTO roles "
            "(organization_id, name, description, is_system, created_at, updated_at) "
            "VALUES (:o, :n, :d, :s, :c, :u)"
        ),
        {"o": org_id, "n": name, "d": description, "s": is_system, "c": now, "u": now},
    )
    role_id = bind.execute(
        sa.text("SELECT id FROM roles WHERE organization_id = :o AND name = :n"),
        {"o": org_id, "n": name},
    ).scalar()
    for perm in permissions:
        bind.execute(
            sa.text(
                "INSERT INTO role_permissions (role_id, permission) VALUES (:r, :p)"
            ),
            {"r": role_id, "p": perm},
        )


def upgrade() -> None:
    op.create_table(
        "roles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("is_system", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "name", name="uq_roles_org_name"),
    )
    op.create_index(op.f("ix_roles_id"), "roles", ["id"], unique=False)
    op.create_index(
        op.f("ix_roles_organization_id"), "roles", ["organization_id"], unique=False
    )

    op.create_table(
        "role_permissions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("permission", sa.String(length=50), nullable=False),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "role_id", "permission", name="uq_role_permissions_role_perm"
        ),
    )
    op.create_index(
        op.f("ix_role_permissions_id"), "role_permissions", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_role_permissions_role_id"),
        "role_permissions",
        ["role_id"],
        unique=False,
    )

    # --- Backfill ----------------------------------------------------------
    bind = op.get_bind()
    org_ids = [row[0] for row in bind.execute(sa.text("SELECT id FROM organizations"))]
    for org_id in org_ids:
        _insert_role(bind, org_id, "Admin", "Full access to every part of the system.",
                     True, ALL_PERMISSIONS)
        _insert_role(bind, org_id, "User", "Basic read-only access.",
                     True, DEFAULT_USER_PERMISSIONS)

        legacy_names = [
            row[0]
            for row in bind.execute(
                sa.text(
                    "SELECT DISTINCT role FROM users "
                    "WHERE organization_id = :o AND role IS NOT NULL"
                ),
                {"o": org_id},
            )
        ]
        for name in legacy_names:
            if name in SYSTEM_ROLE_NAMES:
                continue
            # Known legacy roles carry their historical grant; any other stray
            # name becomes an empty custom role so it still exists to edit.
            perms = LEGACY_GRANTS.get(name, [])
            _insert_role(bind, org_id, name, None, False, perms)


def downgrade() -> None:
    op.drop_index(op.f("ix_role_permissions_role_id"), table_name="role_permissions")
    op.drop_index(op.f("ix_role_permissions_id"), table_name="role_permissions")
    op.drop_table("role_permissions")
    op.drop_index(op.f("ix_roles_organization_id"), table_name="roles")
    op.drop_index(op.f("ix_roles_id"), table_name="roles")
    op.drop_table("roles")
