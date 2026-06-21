"""Add insider_signals table for injury, sharp-money, weather, referee signals

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-21
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "insider_signals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fixture_id", sa.Integer(), nullable=False, index=True),
        sa.Column("sport", sa.String(30), nullable=False, index=True),
        sa.Column("signal_type", sa.String(40), nullable=False, index=True),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("direction", sa.String(20), nullable=True),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("source", sa.String(80), nullable=False, server_default="manual"),
        sa.Column("captured_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("extra", sa.JSON(), nullable=True),
        sa.UniqueConstraint("fixture_id", "signal_type", "source",
                            name="uq_insider_signal"),
    )
    op.create_index("ix_insider_signals_fixture_id", "insider_signals", ["fixture_id"])
    op.create_index("ix_insider_signals_sport",      "insider_signals", ["sport"])
    op.create_index("ix_insider_signals_signal_type","insider_signals", ["signal_type"])
    op.create_index("ix_insider_signals_captured_at","insider_signals", ["captured_at"])


def downgrade() -> None:
    op.drop_table("insider_signals")
