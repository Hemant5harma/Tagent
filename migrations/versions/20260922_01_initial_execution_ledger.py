"""Create the authoritative execution ledger tables.

Revision ID: 20260922_01
Revises:
Create Date: 2026-09-22 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260922_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "proposals",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("account_id", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("instruction", sa.JSON(), nullable=False),
        sa.Column("validation", sa.JSON(), nullable=False),
        sa.Column("order_type", sa.String(length=32), nullable=False),
        sa.Column("confirmation_token", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("confirmation_token"),
    )
    op.create_index("ix_proposals_account_id", "proposals", ["account_id"], unique=False)
    op.create_index("ix_proposals_deadline_at", "proposals", ["deadline_at"], unique=False)
    op.create_index("ix_proposals_expires_at", "proposals", ["expires_at"], unique=False)
    op.create_index("ix_proposals_owner_id", "proposals", ["owner_id"], unique=False)
    op.create_index("ix_proposals_status", "proposals", ["status"], unique=False)

    op.create_table(
        "execution_intents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("proposal_id", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("broker_order_id", sa.String(length=128), nullable=True),
        sa.Column("broker_deal_id", sa.String(length=128), nullable=True),
        sa.Column("broker_position_id", sa.String(length=128), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["proposal_id"], ["proposals.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index(
        "ix_execution_intents_broker_order_id",
        "execution_intents",
        ["broker_order_id"],
        unique=False,
    )
    op.create_index(
        "ix_execution_intents_broker_position_id",
        "execution_intents",
        ["broker_position_id"],
        unique=False,
    )
    op.create_index(
        "ix_execution_intents_proposal_id", "execution_intents", ["proposal_id"], unique=True
    )
    op.create_index("ix_execution_intents_status", "execution_intents", ["status"], unique=False)

    op.create_table(
        "ledger_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("proposal_id", sa.String(length=36), nullable=True),
        sa.Column("execution_intent_id", sa.String(length=36), nullable=True),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ledger_events_created_at", "ledger_events", ["created_at"], unique=False)
    op.create_index(
        "ix_ledger_events_execution_intent_id",
        "ledger_events",
        ["execution_intent_id"],
        unique=False,
    )
    op.create_index("ix_ledger_events_kind", "ledger_events", ["kind"], unique=False)
    op.create_index("ix_ledger_events_proposal_id", "ledger_events", ["proposal_id"], unique=False)

    op.create_table(
        "notification_outbox",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("dedupe_key", sa.String(length=255), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key"),
    )
    op.create_index(
        "ix_notification_outbox_delivered_at", "notification_outbox", ["delivered_at"], unique=False
    )
    op.create_index(
        "ix_notification_outbox_owner_id", "notification_outbox", ["owner_id"], unique=False
    )

    op.create_table(
        "incoming_updates",
        sa.Column("update_id", sa.String(length=128), nullable=False),
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("update_id"),
    )
    op.create_index("ix_incoming_updates_owner_id", "incoming_updates", ["owner_id"], unique=False)

    op.create_table(
        "system_state",
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("system_state")
    op.drop_index("ix_incoming_updates_owner_id", table_name="incoming_updates")
    op.drop_table("incoming_updates")
    op.drop_index("ix_notification_outbox_owner_id", table_name="notification_outbox")
    op.drop_index("ix_notification_outbox_delivered_at", table_name="notification_outbox")
    op.drop_table("notification_outbox")
    op.drop_index("ix_ledger_events_proposal_id", table_name="ledger_events")
    op.drop_index("ix_ledger_events_kind", table_name="ledger_events")
    op.drop_index("ix_ledger_events_execution_intent_id", table_name="ledger_events")
    op.drop_index("ix_ledger_events_created_at", table_name="ledger_events")
    op.drop_table("ledger_events")
    op.drop_index("ix_execution_intents_status", table_name="execution_intents")
    op.drop_index("ix_execution_intents_proposal_id", table_name="execution_intents")
    op.drop_index("ix_execution_intents_broker_position_id", table_name="execution_intents")
    op.drop_index("ix_execution_intents_broker_order_id", table_name="execution_intents")
    op.drop_table("execution_intents")
    op.drop_index("ix_proposals_status", table_name="proposals")
    op.drop_index("ix_proposals_owner_id", table_name="proposals")
    op.drop_index("ix_proposals_expires_at", table_name="proposals")
    op.drop_index("ix_proposals_deadline_at", table_name="proposals")
    op.drop_index("ix_proposals_account_id", table_name="proposals")
    op.drop_table("proposals")
