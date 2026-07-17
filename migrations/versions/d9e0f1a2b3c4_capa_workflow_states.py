"""capa workflow states: gated lifecycle, initiating_cause, reopen/cancel perms

Revision ID: d9e0f1a2b3c4
Revises: a3b4c5d6e7f8
Create Date: 2026-07-17 10:00:00.000000

Replaces the CAPA module's free-form status with a gated lifecycle:
Draft -> Investigation -> Action_Plan -> Implementation ->
Effectiveness_Check -> Closed, plus the terminal Failed_Effectiveness and
Cancelled states and a reason-logged Reopen (from Closed or
Failed_Effectiveness back to Investigation).

Adds ``capas.initiating_cause`` (required, directly or via a linked event,
before leaving Draft) and backfills existing rows onto the new status values.
Also grants the new ``capa:reopen``/``capa:cancel`` permissions to the seeded
Admin and QualityManager roles. Permission strings and status values are
embedded as literals on purpose — migrations must not import application
enums, which may drift from the schema over time.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd9e0f1a2b3c4'
down_revision: Union[str, None] = 'a3b4c5d6e7f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ROLE_CAPA_WORKFLOW_GRANTS = {
    "Admin": ["capa:reopen", "capa:cancel"],
    "QualityManager": ["capa:reopen", "capa:cancel"],
}

# Old status value -> new status value.
_STATUS_UPGRADE = {
    "Open": "Draft",
    "In_Progress": "Implementation",
    "Pending_Verification": "Effectiveness_Check",
}

# New status value -> old status value (best-effort reverse mapping).
_STATUS_DOWNGRADE = {
    "Draft": "Open",
    "Investigation": "In_Progress",
    "Action_Plan": "In_Progress",
    "Implementation": "In_Progress",
    "Effectiveness_Check": "Pending_Verification",
    "Failed_Effectiveness": "In_Progress",
}


def upgrade() -> None:
    op.add_column("capas", sa.Column("initiating_cause", sa.Text(), nullable=True))

    bind = op.get_bind()
    for old_status, new_status in _STATUS_UPGRADE.items():
        bind.execute(
            sa.text("UPDATE capas SET status = :new WHERE status = :old"),
            {"new": new_status, "old": old_status},
        )

    for name, perms in ROLE_CAPA_WORKFLOW_GRANTS.items():
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
    capa_workflow_perms = ["capa:reopen", "capa:cancel"]
    bind.execute(
        sa.text("DELETE FROM role_permissions WHERE permission IN :perms").bindparams(
            sa.bindparam("perms", expanding=True)
        ),
        {"perms": capa_workflow_perms},
    )

    for new_status, old_status in _STATUS_DOWNGRADE.items():
        bind.execute(
            sa.text("UPDATE capas SET status = :old WHERE status = :new"),
            {"old": old_status, "new": new_status},
        )

    op.drop_column("capas", "initiating_cause")
