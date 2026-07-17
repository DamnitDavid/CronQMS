"""rename Non_Conformance event type to Defect; retire Audit_Finding

Revision ID: a7b1c2d3e4f5
Revises: f9a0b1c2d3e4
Create Date: 2026-07-17 00:00:00.000000

Data-only migration. The event-type taxonomy changes:
- ``Non_Conformance`` becomes ``Defect`` (relabeled "Defects" in the UI).
- ``Audit_Finding`` is retired (findings live under the Audits module); any
  existing rows are folded into ``Other``.

Applies to both ``events.event_type`` and the per-type ``custom_fields.event_type``.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7b1c2d3e4f5'
down_revision: Union[str, None] = 'f9a0b1c2d3e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table in ("events", "custom_fields"):
        op.execute(
            sa.text(
                f"UPDATE {table} SET event_type = 'Defect' "
                "WHERE event_type = 'Non_Conformance'"
            )
        )
        op.execute(
            sa.text(
                f"UPDATE {table} SET event_type = 'Other' "
                "WHERE event_type = 'Audit_Finding'"
            )
        )


def downgrade() -> None:
    # Reverse the rename. The Audit_Finding -> Other fold is lossy and cannot be
    # distinguished from genuine "Other" rows, so it is not reversed.
    for table in ("events", "custom_fields"):
        op.execute(
            sa.text(
                f"UPDATE {table} SET event_type = 'Non_Conformance' "
                "WHERE event_type = 'Defect'"
            )
        )
