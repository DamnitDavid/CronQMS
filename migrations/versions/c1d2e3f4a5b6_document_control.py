"""document control: documents + document_versions with retention & workflow

Revision ID: c1d2e3f4a5b6
Revises: b7d1e9f4a2c8
Create Date: 2026-07-16 13:00:00.000000

Adds the controlled-document module:

* ``documents`` — the logical controlled record (number, title, category,
  owner, review/retention policy), and
* ``document_versions`` — versioned revisions carrying the file blob metadata
  and the two-stage review/approval workflow state.

Also grants the new ``document:*`` permissions to the seeded roles so existing
role rows gain document access without a manual edit. Permission strings are
embedded as literals on purpose — migrations must not import application enums,
which may drift from the schema over time.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c1d2e3f4a5b6'
down_revision: Union[str, None] = 'b7d1e9f4a2c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Document permission grants per seeded role name (mirrors ROLE_PERMISSIONS).
ROLE_DOCUMENT_GRANTS = {
    "Admin": [
        "document:create", "document:read", "document:update", "document:review",
        "document:approve", "document:obsolete", "document:delete",
    ],
    "QualityManager": [
        "document:create", "document:read", "document:update", "document:review",
        "document:approve", "document:obsolete", "document:delete",
    ],
    "Investigator": ["document:create", "document:read", "document:update"],
    "Approver": ["document:read", "document:review", "document:approve", "document:obsolete"],
    "Viewer": ["document:read"],
    "User": ["document:read"],
}


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("document_number", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("category", sa.String(length=30), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("owner_id", sa.Integer(), nullable=True),
        sa.Column("review_period_months", sa.Integer(), nullable=True),
        sa.Column("next_review_date", sa.Date(), nullable=True),
        sa.Column("retention_period_months", sa.Integer(), nullable=True),
        sa.Column("retention_until", sa.Date(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_documents_id"), "documents", ["id"], unique=False)
    op.create_index(
        op.f("ix_documents_organization_id"), "documents", ["organization_id"], unique=False
    )
    op.create_index(
        op.f("ix_documents_document_number"), "documents", ["document_number"], unique=False
    )
    op.create_index(op.f("ix_documents_owner_id"), "documents", ["owner_id"], unique=False)

    op.create_table(
        "document_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("change_summary", sa.Text(), nullable=True),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=120), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("author_id", sa.Integer(), nullable=False),
        sa.Column("reviewed_by", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("approved_by", sa.Integer(), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["approved_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key", name="uq_document_versions_storage_key"),
    )
    op.create_index(
        op.f("ix_document_versions_id"), "document_versions", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_document_versions_organization_id"),
        "document_versions",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_document_versions_document_id"),
        "document_versions",
        ["document_id"],
        unique=False,
    )

    # --- Grant document permissions to seeded roles ------------------------
    bind = op.get_bind()
    for name, perms in ROLE_DOCUMENT_GRANTS.items():
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
    document_perms = [
        "document:create", "document:read", "document:update", "document:review",
        "document:approve", "document:obsolete", "document:delete",
    ]
    bind.execute(
        sa.text("DELETE FROM role_permissions WHERE permission IN :perms").bindparams(
            sa.bindparam("perms", expanding=True)
        ),
        {"perms": document_perms},
    )

    op.drop_index(op.f("ix_document_versions_document_id"), table_name="document_versions")
    op.drop_index(
        op.f("ix_document_versions_organization_id"), table_name="document_versions"
    )
    op.drop_index(op.f("ix_document_versions_id"), table_name="document_versions")
    op.drop_table("document_versions")
    op.drop_index(op.f("ix_documents_owner_id"), table_name="documents")
    op.drop_index(op.f("ix_documents_document_number"), table_name="documents")
    op.drop_index(op.f("ix_documents_organization_id"), table_name="documents")
    op.drop_index(op.f("ix_documents_id"), table_name="documents")
    op.drop_table("documents")
