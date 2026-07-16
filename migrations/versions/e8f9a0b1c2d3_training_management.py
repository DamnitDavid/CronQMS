"""training management: employees + training courses + training records

Revision ID: e8f9a0b1c2d3
Revises: d7e8f9a0b1c2
Create Date: 2026-07-16 16:00:00.000000

Adds the Training Management module:

* ``employees`` — baseline operation employees with no login account (the
  shop-floor operators trained on SOPs via a tablet),
* ``training_courses`` — a defined training, optionally tied to a controlled
  SOP ``documents`` row, with an optional recertification period, and
* ``training_records`` — one assignment of a course to one trainee (either an
  employee or a system user), tracking status, trainer sign-off, a typed
  trainee acknowledgment, and a computed expiry date.

Also grants the new ``training:*`` permissions to the seeded roles so existing
role rows gain training access without a manual edit. Permission strings are
embedded as literals on purpose — migrations must not import application enums,
which may drift from the schema over time.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e8f9a0b1c2d3'
down_revision: Union[str, None] = 'd7e8f9a0b1c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Training permission grants per seeded role name (mirrors ROLE_PERMISSIONS).
ALL_TRAINING_PERMS = [
    "training:create", "training:read", "training:update",
    "training:certify", "training:delete",
]
ROLE_TRAINING_GRANTS = {
    "Admin": ALL_TRAINING_PERMS,
    "QualityManager": ALL_TRAINING_PERMS,
    "Investigator": ["training:create", "training:read", "training:update", "training:certify"],
    "Approver": ["training:read", "training:certify"],
    "Viewer": ["training:read"],
    "User": ["training:read"],
}


def upgrade() -> None:
    op.create_table(
        "employees",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("employee_number", sa.String(length=50), nullable=True),
        sa.Column("department", sa.String(length=255), nullable=True),
        sa.Column("job_title", sa.String(length=255), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_employees_id"), "employees", ["id"], unique=False)
    op.create_index(
        op.f("ix_employees_organization_id"), "employees", ["organization_id"], unique=False
    )

    op.create_table(
        "training_courses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("document_id", sa.Integer(), nullable=True),
        sa.Column("recertification_period_months", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_training_courses_id"), "training_courses", ["id"], unique=False)
    op.create_index(
        op.f("ix_training_courses_organization_id"),
        "training_courses",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_training_courses_code"), "training_courses", ["code"], unique=False
    )
    op.create_index(
        op.f("ix_training_courses_document_id"),
        "training_courses",
        ["document_id"],
        unique=False,
    )

    op.create_table(
        "training_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("assigned_date", sa.Date(), nullable=False),
        sa.Column("trained_date", sa.Date(), nullable=True),
        sa.Column("trained_by", sa.Integer(), nullable=True),
        sa.Column("trainee_acknowledgment", sa.String(length=255), nullable=True),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["course_id"], ["training_courses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["trained_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_training_records_id"), "training_records", ["id"], unique=False)
    op.create_index(
        op.f("ix_training_records_organization_id"),
        "training_records",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_training_records_course_id"), "training_records", ["course_id"], unique=False
    )
    op.create_index(
        op.f("ix_training_records_employee_id"),
        "training_records",
        ["employee_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_training_records_user_id"), "training_records", ["user_id"], unique=False
    )

    # --- Grant training permissions to seeded roles ------------------------
    bind = op.get_bind()
    for name, perms in ROLE_TRAINING_GRANTS.items():
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
        {"perms": ALL_TRAINING_PERMS},
    )

    op.drop_index(op.f("ix_training_records_user_id"), table_name="training_records")
    op.drop_index(op.f("ix_training_records_employee_id"), table_name="training_records")
    op.drop_index(op.f("ix_training_records_course_id"), table_name="training_records")
    op.drop_index(op.f("ix_training_records_organization_id"), table_name="training_records")
    op.drop_index(op.f("ix_training_records_id"), table_name="training_records")
    op.drop_table("training_records")

    op.drop_index(op.f("ix_training_courses_document_id"), table_name="training_courses")
    op.drop_index(op.f("ix_training_courses_code"), table_name="training_courses")
    op.drop_index(op.f("ix_training_courses_organization_id"), table_name="training_courses")
    op.drop_index(op.f("ix_training_courses_id"), table_name="training_courses")
    op.drop_table("training_courses")

    op.drop_index(op.f("ix_employees_organization_id"), table_name="employees")
    op.drop_index(op.f("ix_employees_id"), table_name="employees")
    op.drop_table("employees")
