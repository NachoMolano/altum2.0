"""Initial tables

Revision ID: 001
Revises:
Create Date: 2026-04-13

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("instagram_user_id", sa.String(), nullable=False, index=True),
        sa.Column("state", sa.String(), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "messages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("conversation_id", UUID(as_uuid=True), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("instagram_message_id", sa.String(), nullable=True, unique=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "prospect_profiles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("conversation_id", UUID(as_uuid=True), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("nombre", sa.String(), nullable=True),
        sa.Column("empresa", sa.String(), nullable=True),
        sa.Column("sector", sa.String(), nullable=True),
        sa.Column("necesidad_principal", sa.Text(), nullable=True),
        sa.Column("presencia_digital", sa.String(), nullable=True),
        sa.Column("tiene_identidad_marca", sa.String(), nullable=True),
        sa.Column("objetivo_principal", sa.Text(), nullable=True),
        sa.Column("presupuesto_aprox", sa.String(), nullable=True),
        sa.Column("telefono", sa.String(), nullable=True),
        sa.Column("sheets_synced", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("prospect_profiles")
    op.drop_table("messages")
    op.drop_table("conversations")
