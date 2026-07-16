"""audit management: audits + checklist items + findings

Revision ID: d7e8f9a0b1c2
Revises: c1d2e3f4a5b6
Create Date: 2026-07-16 14:00:00.000000

Adds the Audit Management module:

* ``audits`` — a planned internal/external/supplier audit (reference, title,
  type, status, scope, standard, lead auditor, auditee, schedule, summary),
* ``audit_checklist_items`` — the questions/clauses evaluated during the audit
  and their recorded results, and
* ``audit_findings`` — nonconformities/observations raised, optionally tied to
  a checklist item and linked to a CAPA for correction.

Also grants the new ``audit:*`` permissions to the seeded roles so existing
role rows gain audit access without a manual edit. Permission strings are
embedded as literals on purpose — migrations must not import application enums,
which may drift from the schema over time.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd7e8f9a0b1c2'
down_revision: Union[str, None] = 'c1d2e3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Audit permission grants per seeded role name (mirrors ROLE_PERMISSIONS).
ALL_AUDIT_PERMS = [
    "audit:create", "audit:read", "audit:update",
    "audit:conduct", "audit:close", "audit:delete",
]
ROLE_AUDIT_GRANTS = {
    "Admin": ALL_AUDIT_PERMS,
    "QualityManager": ALL_AUDIT_PERMS,
    "Investigator": ["audit:create", "audit:read", "audit:update", "audit:conduct"],
    "Approver": ["audit:read", "audit:close"],
    "Viewer": ["audit:read"],
    "User": ["audit:read"],
}


def upgrade() -> None:
    op.create_table(
        "audits",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("reference", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("audit_type", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("scope", sa.Text(), nullable=True),
        sa.Column("standard", sa.String(length=255), nullable=True),
        sa.Column("lead_auditor_id", sa.Integer(), nullable=True),
        sa.Column("auditee", sa.String(length=255), nullable=True),
        sa.Column("planned_date", sa.Date(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["lead_auditor_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_audits_id"), "audits", ["id"], unique=False)
    op.create_index(
        op.f("ix_audits_organization_id"), "audits", ["organization_id"], unique=False
    )
    op.create_index(op.f("ix_audits_reference"), "audits", ["reference"], unique=False)
    op.create_index(
        op.f("ix_audits_lead_auditor_id"), "audits", ["lead_auditor_id"], unique=False
    )

    op.create_table(
        "audit_checklist_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("audit_id", sa.Integer(), nullable=False),
        sa.Column("clause", sa.String(length=100), nullable=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("result", sa.String(length=20), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["audit_id"], ["audits.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_audit_checklist_items_id"), "audit_checklist_items", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_audit_checklist_items_organization_id"),
        "audit_checklist_items",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_audit_checklist_items_audit_id"),
        "audit_checklist_items",
        ["audit_id"],
        unique=False,
    )

    op.create_table(
        "audit_findings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("audit_id", sa.Integer(), nullable=False),
        sa.Column("checklist_item_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("capa_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["audit_id"], ["audits.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["checklist_item_id"], ["audit_checklist_items.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["capa_id"], ["capas.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_audit_findings_id"), "audit_findings", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_audit_findings_organization_id"),
        "audit_findings",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_audit_findings_audit_id"), "audit_findings", ["audit_id"], unique=False
    )
    op.create_index(
        op.f("ix_audit_findings_owner_id"), "audit_findings", ["owner_id"], unique=False
    )

    # --- Grant audit permissions to seeded roles ---------------------------
    bind = op.get_bind()
    for name, perms in ROLE_AUDIT_GRANTS.items():
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
        {"perms": ALL_AUDIT_PERMS},
    )

    op.drop_index(op.f("ix_audit_findings_owner_id"), table_name="audit_findings")
    op.drop_index(op.f("ix_audit_findings_audit_id"), table_name="audit_findings")
    op.drop_index(op.f("ix_audit_findings_organization_id"), table_name="audit_findings")
    op.drop_index(op.f("ix_audit_findings_id"), table_name="audit_findings")
    op.drop_table("audit_findings")

    op.drop_index(
        op.f("ix_audit_checklist_items_audit_id"), table_name="audit_checklist_items"
    )
    op.drop_index(
        op.f("ix_audit_checklist_items_organization_id"),
        table_name="audit_checklist_items",
    )
    op.drop_index(
        op.f("ix_audit_checklist_items_id"), table_name="audit_checklist_items"
    )
    op.drop_table("audit_checklist_items")

    op.drop_index(op.f("ix_audits_lead_auditor_id"), table_name="audits")
    op.drop_index(op.f("ix_audits_reference"), table_name="audits")
    op.drop_index(op.f("ix_audits_organization_id"), table_name="audits")
    op.drop_index(op.f("ix_audits_id"), table_name="audits")
    op.drop_table("audits")
