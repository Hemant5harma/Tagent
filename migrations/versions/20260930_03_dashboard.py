"""Durable inbox, reporting and isolated dashboard authentication.

Revision ID: 20260930_03
Revises: 20260923_02
"""

import sqlalchemy as sa
from alembic import op

revision = "20260930_03"
down_revision = "20260923_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("proposals", sa.Column("source_update_id", sa.String(128), nullable=True))
    op.create_index("ix_proposals_source_update_id", "proposals", ["source_update_id"], unique=True)
    for name, kind in [
        ("payload", sa.JSON()),
        ("reply", sa.JSON()),
        ("delivered_at", sa.DateTime(timezone=True)),
    ]:
        op.add_column("incoming_updates", sa.Column(name, kind, nullable=True))
    op.create_table(
        "dashboard_cache",
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "broker_deals",
        sa.Column("id", sa.String(160), primary_key=True),
        sa.Column("account_id", sa.String(128), nullable=False),
        sa.Column("position_id", sa.String(128), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_table(
        "equity_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.String(128), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_table(
        "agent_activity",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "dashboard_access",
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    for table, columns in {
        "broker_deals": ["account_id", "position_id", "occurred_at"],
        "equity_snapshots": ["account_id", "observed_at"],
        "agent_activity": ["kind", "created_at"],
        "dashboard_access": ["kind", "expires_at"],
    }.items():
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    for table in [
        "dashboard_access",
        "agent_activity",
        "equity_snapshots",
        "broker_deals",
        "dashboard_cache",
    ]:
        op.drop_table(table)
    for column in ["payload", "reply", "delivered_at"]:
        op.drop_column("incoming_updates", column)
    op.drop_index("ix_proposals_source_update_id", "proposals")
    op.drop_column("proposals", "source_update_id")
