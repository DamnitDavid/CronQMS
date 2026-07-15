"""custom fields per event type and their per-event values

Revision ID: a1b2c3d4e5f6
Revises: fdffb66d1814
Create Date: 2026-07-15 12:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'fdffb66d1814'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'custom_fields',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('event_type', sa.String(length=50), nullable=False),
        sa.Column('label', sa.String(length=255), nullable=False),
        sa.Column('key', sa.String(length=100), nullable=False),
        sa.Column('field_type', sa.String(length=20), nullable=False),
        sa.Column('display_order', sa.Integer(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_custom_fields_id'), 'custom_fields', ['id'], unique=False)
    op.create_index(op.f('ix_custom_fields_organization_id'), 'custom_fields', ['organization_id'], unique=False)
    op.create_index(op.f('ix_custom_fields_event_type'), 'custom_fields', ['event_type'], unique=False)

    op.create_table(
        'event_custom_values',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('event_id', sa.Integer(), nullable=False),
        sa.Column('custom_field_id', sa.Integer(), nullable=False),
        sa.Column('value', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['event_id'], ['events.id'], ),
        sa.ForeignKeyConstraint(['custom_field_id'], ['custom_fields.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_event_custom_values_id'), 'event_custom_values', ['id'], unique=False)
    op.create_index(op.f('ix_event_custom_values_event_id'), 'event_custom_values', ['event_id'], unique=False)
    op.create_index(op.f('ix_event_custom_values_custom_field_id'), 'event_custom_values', ['custom_field_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_event_custom_values_custom_field_id'), table_name='event_custom_values')
    op.drop_index(op.f('ix_event_custom_values_event_id'), table_name='event_custom_values')
    op.drop_index(op.f('ix_event_custom_values_id'), table_name='event_custom_values')
    op.drop_table('event_custom_values')
    op.drop_index(op.f('ix_custom_fields_event_type'), table_name='custom_fields')
    op.drop_index(op.f('ix_custom_fields_organization_id'), table_name='custom_fields')
    op.drop_index(op.f('ix_custom_fields_id'), table_name='custom_fields')
    op.drop_table('custom_fields')
