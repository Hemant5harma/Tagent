"""Add confirmed actions, durable mutations, and update completion state.

Revision ID: 20260923_02
Revises: 20260922_01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260923_02"
down_revision = "20260922_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "incoming_updates",
        sa.Column("status", sa.String(length=32), nullable=False, server_default="received"),
    )
    op.add_column(
        "incoming_updates", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index("ix_incoming_updates_status", "incoming_updates", ["status"], unique=False)

    op.create_table(
        "action_proposals",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("account_id", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("action_type", sa.String(length=32), nullable=False),
        sa.Column("target_kind", sa.String(length=16), nullable=False),
        sa.Column("target_id", sa.String(length=128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("confirmation_token", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("confirmation_token"),
    )
    for column in ("owner_id", "account_id", "action_type", "target_id", "status", "expires_at"):
        op.create_index(f"ix_action_proposals_{column}", "action_proposals", [column], unique=False)

    op.create_table(
        "mutation_intents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("proposal_id", sa.String(length=36), nullable=True),
        sa.Column("action_proposal_id", sa.String(length=36), nullable=True),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["action_proposal_id"], ["action_proposals.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    for column in ("proposal_id", "action_proposal_id", "operation", "target_id", "status"):
        op.create_index(f"ix_mutation_intents_{column}", "mutation_intents", [column], unique=False)


def downgrade() -> None:
    for column in ("status", "target_id", "operation", "action_proposal_id", "proposal_id"):
        op.drop_index(f"ix_mutation_intents_{column}", table_name="mutation_intents")
    op.drop_table("mutation_intents")
    for column in ("expires_at", "status", "target_id", "action_type", "account_id", "owner_id"):
        op.drop_index(f"ix_action_proposals_{column}", table_name="action_proposals")
    op.drop_table("action_proposals")
    op.drop_index("ix_incoming_updates_status", table_name="incoming_updates")
    op.drop_column("incoming_updates", "completed_at")
    op.drop_column("incoming_updates", "status")
