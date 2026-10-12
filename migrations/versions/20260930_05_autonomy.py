"""Separate manual trades, autonomous research, and expiring demo grants."""

import sqlalchemy as sa
from alembic import op

revision = "20260930_05"
down_revision = "20260930_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "proposals", sa.Column("origin", sa.String(16), nullable=False, server_default="manual")
    )
    op.add_column("proposals", sa.Column("strategy_version", sa.String(128)))
    op.add_column("proposals", sa.Column("decision_run_id", sa.String(36)))
    op.create_index("ix_proposals_decision_run_id", "proposals", ["decision_run_id"])
    op.create_table(
        "autonomy_grants",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("account_id", sa.String(128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for key in ("owner_id", "account_id", "expires_at"):
        op.create_index(f"ix_autonomy_grants_{key}", "autonomy_grants", [key])
    op.create_table(
        "autonomous_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("account_id", sa.String(128), nullable=False),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("timeframe", sa.String(8), nullable=False),
        sa.Column("bar_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("strategy_version", sa.String(128), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("reports", sa.JSON(), nullable=False),
        sa.Column("decision", sa.JSON(), nullable=False),
        sa.Column("proposal_id", sa.String(36)),
        sa.Column("grant_id", sa.String(36)),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "account_id",
            "strategy_version",
            "symbol",
            "timeframe",
            "bar_at",
            name="uq_autonomy_bar",
        ),
    )
    for key in ("account_id", "status", "proposal_id", "grant_id"):
        op.create_index(f"ix_autonomous_runs_{key}", "autonomous_runs", [key])


def downgrade() -> None:
    op.drop_table("autonomous_runs")
    op.drop_table("autonomy_grants")
    op.drop_index("ix_proposals_decision_run_id", table_name="proposals")
    with op.batch_alter_table("proposals") as batch:
        batch.drop_column("decision_run_id")
        batch.drop_column("strategy_version")
        batch.drop_column("origin")
