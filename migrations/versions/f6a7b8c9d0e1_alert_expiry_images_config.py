"""alert expiry + nullable event, alert images, and org settings

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-07-15 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Alert: expiry date + event_id becomes optional (standalone alerts).
    with op.batch_alter_table("alerts") as batch:
        batch.add_column(sa.Column("expires_at", sa.Date(), nullable=True))
        batch.alter_column("event_id", existing_type=sa.Integer(), nullable=True)

    op.create_table(
        'alert_images',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('alert_id', sa.Integer(), nullable=False),
        sa.Column('position', sa.SmallInteger(), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('content_type', sa.String(length=255), nullable=True),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('checksum', sa.String(length=64), nullable=False),
        sa.Column('storage_key', sa.String(length=255), nullable=False),
        sa.Column('uploaded_by', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['alert_id'], ['alerts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['uploaded_by'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('alert_id', 'position', name='uq_alert_images_alert_position'),
    )
    op.create_index(op.f('ix_alert_images_id'), 'alert_images', ['id'], unique=False)
    op.create_index(op.f('ix_alert_images_alert_id'), 'alert_images', ['alert_id'], unique=False)

    op.create_table(
        'org_settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('key', sa.String(length=100), nullable=False),
        sa.Column('value', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('organization_id', 'key', name='uq_org_settings_org_key'),
    )
    op.create_index(op.f('ix_org_settings_id'), 'org_settings', ['id'], unique=False)
    op.create_index(op.f('ix_org_settings_organization_id'), 'org_settings', ['organization_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_org_settings_organization_id'), table_name='org_settings')
    op.drop_index(op.f('ix_org_settings_id'), table_name='org_settings')
    op.drop_table('org_settings')

    op.drop_index(op.f('ix_alert_images_alert_id'), table_name='alert_images')
    op.drop_index(op.f('ix_alert_images_id'), table_name='alert_images')
    op.drop_table('alert_images')

    with op.batch_alter_table("alerts") as batch:
        batch.alter_column("event_id", existing_type=sa.Integer(), nullable=False)
        batch.drop_column("expires_at")
