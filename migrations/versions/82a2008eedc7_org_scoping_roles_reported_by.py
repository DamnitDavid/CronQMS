"""org scoping, roles, reported_by

Revision ID: 82a2008eedc7
Revises: 759b083671fd
Create Date: 2026-07-15 01:11:19.321310

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '82a2008eedc7'
down_revision: Union[str, None] = '759b083671fd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- New access-scope tables -------------------------------------------
    op.create_table(
        "organizations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_organizations_id"), "organizations", ["id"], unique=False)
    op.create_index(op.f("ix_organizations_code"), "organizations", ["code"], unique=True)

    op.create_table(
        "sites",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_sites_id"), "sites", ["id"], unique=False)
    op.create_index(op.f("ix_sites_organization_id"), "sites", ["organization_id"], unique=False)

    # --- Scope users to an organization ------------------------------------
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("organization_id", sa.Integer(), nullable=True))
        batch.create_index(op.f("ix_users_organization_id"), ["organization_id"], unique=False)
        batch.create_foreign_key(
            "fk_users_organization_id", "organizations", ["organization_id"], ["id"]
        )

    # --- Rework events: reported_by, org/site FKs, drop free-text facility --
    # This baseline migration assumes the events table is empty, so
    # organization_id is added NOT NULL directly (no backfill step needed).
    with op.batch_alter_table("events") as batch:
        batch.alter_column("user_id", new_column_name="reported_by")
        batch.add_column(sa.Column("organization_id", sa.Integer(), nullable=False))
        batch.add_column(sa.Column("site_id", sa.Integer(), nullable=True))
        batch.drop_column("facility")
        batch.create_index(op.f("ix_events_organization_id"), ["organization_id"], unique=False)
        batch.create_index(op.f("ix_events_site_id"), ["site_id"], unique=False)
        batch.create_foreign_key(
            "fk_events_organization_id", "organizations", ["organization_id"], ["id"]
        )
        batch.create_foreign_key("fk_events_site_id", "sites", ["site_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("events") as batch:
        batch.drop_constraint("fk_events_site_id", type_="foreignkey")
        batch.drop_constraint("fk_events_organization_id", type_="foreignkey")
        batch.drop_index(op.f("ix_events_site_id"))
        batch.drop_index(op.f("ix_events_organization_id"))
        batch.add_column(sa.Column("facility", sa.String(length=255), nullable=True))
        batch.drop_column("site_id")
        batch.drop_column("organization_id")
        batch.alter_column("reported_by", new_column_name="user_id")

    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("fk_users_organization_id", type_="foreignkey")
        batch.drop_index(op.f("ix_users_organization_id"))
        batch.drop_column("organization_id")

    op.drop_index(op.f("ix_sites_organization_id"), table_name="sites")
    op.drop_index(op.f("ix_sites_id"), table_name="sites")
    op.drop_table("sites")
    op.drop_index(op.f("ix_organizations_code"), table_name="organizations")
    op.drop_index(op.f("ix_organizations_id"), table_name="organizations")
    op.drop_table("organizations")
