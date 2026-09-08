"""market evidence and artifact metadata

Revision ID: 0005_market_evidence
Revises: 0004
Create Date: 2026-09-08
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_market_evidence"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "market_evidence",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sport", sa.String(length=30), nullable=False),
        sa.Column("market", sa.String(length=50), nullable=False),
        sa.Column("settled", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("wins", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("losses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pushes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("recent_settled", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("recent_wins", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("recent_losses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("accuracy", sa.Float(), nullable=True),
        sa.Column("recent_accuracy", sa.Float(), nullable=True),
        sa.Column("brier_score", sa.Float(), nullable=True),
        sa.Column("expected_value", sa.Float(), nullable=True),
        sa.Column("roi_units", sa.Float(), nullable=True),
        sa.Column("last_loss_streak", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("publication_blocked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("block_reasons", sa.JSON(), nullable=True),
        sa.Column("is_model_trained", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("sport", "market", name="uq_market_evidence"),
    )
    op.create_index("ix_market_evidence_sport", "market_evidence", ["sport"])
    op.create_index("ix_market_evidence_market", "market_evidence", ["market"])
    op.create_index("ix_market_evidence_updated_at", "market_evidence", ["updated_at"])

    op.execute("ALTER TABLE model_artifacts ADD COLUMN IF NOT EXISTS metadata_json JSON")


def downgrade() -> None:
    op.drop_index("ix_market_evidence_updated_at", table_name="market_evidence")
    op.drop_index("ix_market_evidence_market", table_name="market_evidence")
    op.drop_index("ix_market_evidence_sport", table_name="market_evidence")
    op.drop_table("market_evidence")
    op.execute("ALTER TABLE model_artifacts DROP COLUMN IF EXISTS metadata_json")