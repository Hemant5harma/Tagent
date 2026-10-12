"""Private bounded conversation memory, separate from the execution ledger."""

import sqlalchemy as sa
from alembic import op

revision = "20260930_04"
down_revision = "20260930_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_memory",
        sa.Column("owner_id", sa.String(256), primary_key=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("conversation_memory")
