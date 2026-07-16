"""change control: change requests + impact assessment + actions

Revision ID: f9a0b1c2d3e4
Revises: e8f9a0b1c2d3
Create Date: 2026-07-16 16:00:00.000000

Adds the Change Control (Management of Change) module:

* ``change_requests`` — a proposed process/product change (reference, title,
  type, status, description, reason, affected area, risk level, owner, target
  and actual implementation dates, post-implementation review),
* ``change_impacts`` — the impact-assessment rows evaluated for the change
  (area, impact level, description, mitigation), and
* ``change_actions`` — implementation actions raised to carry the change out,
  optionally linked to a CAPA for tracking.

Also grants the new ``change:*`` permissions to the seeded roles so existing
role rows gain change-control access without a manual edit. Permission strings
are embedded as literals on purpose — migrations must not import application
enums, which may drift from the schema over time.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f9a0b1c2d3e4'
down_revision: Union[str, None] = 'e8f9a0b1c2d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Change permission grants per seeded role name (mirrors ROLE_PERMISSIONS).
ALL_CHANGE_PERMS = [
    "change:create", "change:read", "change:update",
    "change:assess", "change:approve", "change:delete",
]
ROLE_CHANGE_GRANTS = {
    "Admin": ALL_CHANGE_PERMS,
    "QualityManager": ALL_CHANGE_PERMS,
    "Investigator": ["change:create", "change:read", "change:update", "change:assess"],
    "Approver": ["change:read", "change:approve"],
    "Viewer": ["change:read"],
    "User": ["change:read"],
}


def upgrade() -> None:
    op.create_table(
        "change_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("reference", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("change_type", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("affected_area", sa.String(length=255), nullable=True),
        sa.Column("risk_level", sa.String(length=20), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=True),
        sa.Column("target_date", sa.Date(), nullable=True),
        sa.Column("implementation_date", sa.Date(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_change_requests_id"), "change_requests", ["id"], unique=False)
    op.create_index(
        op.f("ix_change_requests_organization_id"),
        "change_requests",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_change_requests_reference"), "change_requests", ["reference"], unique=False
    )
    op.create_index(
        op.f("ix_change_requests_owner_id"), "change_requests", ["owner_id"], unique=False
    )

    op.create_table(
        "change_impacts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("change_id", sa.Integer(), nullable=False),
        sa.Column("area", sa.String(length=30), nullable=False),
        sa.Column("impact_level", sa.String(length=20), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("mitigation", sa.Text(), nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["change_id"], ["change_requests.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_change_impacts_id"), "change_impacts", ["id"], unique=False)
    op.create_index(
        op.f("ix_change_impacts_organization_id"),
        "change_impacts",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_change_impacts_change_id"), "change_impacts", ["change_id"], unique=False
    )

    op.create_table(
        "change_actions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("change_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("capa_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["change_id"], ["change_requests.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["capa_id"], ["capas.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_change_actions_id"), "change_actions", ["id"], unique=False)
    op.create_index(
        op.f("ix_change_actions_organization_id"),
        "change_actions",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_change_actions_change_id"), "change_actions", ["change_id"], unique=False
    )
    op.create_index(
        op.f("ix_change_actions_owner_id"), "change_actions", ["owner_id"], unique=False
    )

    # --- Grant change permissions to seeded roles --------------------------
    bind = op.get_bind()
    for name, perms in ROLE_CHANGE_GRANTS.items():
        role_ids = [
            row[0]
            for row in bind.execute(
                sa.text("SELECT id FROM roles WHERE name = :n"), {"n": name}
            )
        ]
        for role_id in role_ids:
            for perm in perms:
                exists = bind.execute(
                    sa.text(
                        "SELECT 1 FROM role_permissions "
                        "WHERE role_id = :r AND permission = :p"
                    ),
                    {"r": role_id, "p": perm},
                ).scalar()
                if exists is None:
                    bind.execute(
                        sa.text(
                            "INSERT INTO role_permissions (role_id, permission) "
                            "VALUES (:r, :p)"
                        ),
                        {"r": role_id, "p": perm},
                    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text("DELETE FROM role_permissions WHERE permission IN :perms").bindparams(
            sa.bindparam("perms", expanding=True)
        ),
        {"perms": ALL_CHANGE_PERMS},
    )

    op.drop_index(op.f("ix_change_actions_owner_id"), table_name="change_actions")
    op.drop_index(op.f("ix_change_actions_change_id"), table_name="change_actions")
    op.drop_index(op.f("ix_change_actions_organization_id"), table_name="change_actions")
    op.drop_index(op.f("ix_change_actions_id"), table_name="change_actions")
    op.drop_table("change_actions")

    op.drop_index(op.f("ix_change_impacts_change_id"), table_name="change_impacts")
    op.drop_index(op.f("ix_change_impacts_organization_id"), table_name="change_impacts")
    op.drop_index(op.f("ix_change_impacts_id"), table_name="change_impacts")
    op.drop_table("change_impacts")

    op.drop_index(op.f("ix_change_requests_owner_id"), table_name="change_requests")
    op.drop_index(op.f("ix_change_requests_reference"), table_name="change_requests")
    op.drop_index(op.f("ix_change_requests_organization_id"), table_name="change_requests")
    op.drop_index(op.f("ix_change_requests_id"), table_name="change_requests")
    op.drop_table("change_requests")
