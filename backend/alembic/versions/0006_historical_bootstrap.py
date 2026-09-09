"""historical evaluation table and market evidence historical columns

Revision ID: 0006_historical_bootstrap
Revises: 0005
Create Date: 2026-09-09
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_historical_bootstrap"
down_revision = "0005_market_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "historical_evaluation",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fixture_id", sa.Integer(), nullable=False),
        sa.Column("sport", sa.String(length=30), nullable=False),
        sa.Column("league", sa.String(length=80), nullable=True),
        sa.Column("match_date", sa.Date(), nullable=False),
        sa.Column("home_team", sa.String(length=120), nullable=False),
        sa.Column("away_team", sa.String(length=120), nullable=False),
        sa.Column("home_score", sa.Integer(), nullable=True),
        sa.Column("away_score", sa.Integer(), nullable=True),
        sa.Column("market", sa.String(length=50), nullable=False),
        sa.Column("pick", sa.String(length=120), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("edge_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("outcome", sa.String(length=10), nullable=True),
        sa.Column("brier_score", sa.Float(), nullable=True),
        sa.Column("has_odds", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("applied_odds", sa.Float(), nullable=True),
        sa.Column("roi_units", sa.Float(), nullable=True),
        sa.Column("model_version_id", sa.Integer(), nullable=True),
        sa.Column("inference_mode", sa.String(length=40), nullable=False, server_default="walk_forward_fold"),
        sa.Column("fold_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("job_id", sa.String(length=80), nullable=True),
        sa.Column("source", sa.String(length=80), nullable=False, server_default="bootstrap"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("fixture_id", "market", "model_version_id", "fold_index", name="uq_historical_eval"),
    )
    op.create_index("ix_historical_evaluation_fixture_id", "historical_evaluation", ["fixture_id"])
    op.create_index("ix_historical_evaluation_sport", "historical_evaluation", ["sport"])
    op.create_index("ix_historical_evaluation_match_date", "historical_evaluation", ["match_date"])
    op.create_index("ix_historical_evaluation_market", "historical_evaluation", ["market"])
    op.create_index("ix_historical_evaluation_model_version_id", "historical_evaluation", ["model_version_id"])
    op.create_index("ix_historical_evaluation_created_at", "historical_evaluation", ["created_at"])

    op.execute(
        "ALTER TABLE market_evidence ADD COLUMN IF NOT EXISTS historical_settled INTEGER NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE market_evidence ADD COLUMN IF NOT EXISTS historical_wins INTEGER NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE market_evidence ADD COLUMN IF NOT EXISTS historical_losses INTEGER NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE market_evidence ADD COLUMN IF NOT EXISTS historical_accuracy FLOAT"
    )
    op.execute(
        "ALTER TABLE market_evidence ADD COLUMN IF NOT EXISTS historical_brier_sum FLOAT"
    )
    op.execute(
        "ALTER TABLE market_evidence ADD COLUMN IF NOT EXISTS historical_brier_count INTEGER NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE market_evidence ADD COLUMN IF NOT EXISTS historical_odds_count INTEGER NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE market_evidence ADD COLUMN IF NOT EXISTS historical_roi_units FLOAT"
    )
    op.execute(
        "ALTER TABLE market_evidence ADD COLUMN IF NOT EXISTS historical_has_odds BOOLEAN NOT NULL DEFAULT FALSE"
    )
    op.execute(
        "ALTER TABLE market_evidence ADD COLUMN IF NOT EXISTS bootstrap_updated_at TIMESTAMP WITHOUT TIME ZONE"
    )


def downgrade() -> None:
    op.drop_index("ix_historical_evaluation_created_at", table_name="historical_evaluation")
    op.drop_index("ix_historical_evaluation_model_version_id", table_name="historical_evaluation")
    op.drop_index("ix_historical_evaluation_market", table_name="historical_evaluation")
    op.drop_index("ix_historical_evaluation_match_date", table_name="historical_evaluation")
    op.drop_index("ix_historical_evaluation_sport", table_name="historical_evaluation")
    op.drop_index("ix_historical_evaluation_fixture_id", table_name="historical_evaluation")
    op.drop_table("historical_evaluation")

    op.execute("ALTER TABLE market_evidence DROP COLUMN IF EXISTS bootstrap_updated_at")
    op.execute("ALTER TABLE market_evidence DROP COLUMN IF EXISTS historical_has_odds")
    op.execute("ALTER TABLE market_evidence DROP COLUMN IF EXISTS historical_roi_units")
    op.execute("ALTER TABLE market_evidence DROP COLUMN IF EXISTS historical_odds_count")
    op.execute("ALTER TABLE market_evidence DROP COLUMN IF EXISTS historical_brier_count")
    op.execute("ALTER TABLE market_evidence DROP COLUMN IF EXISTS historical_brier_sum")
    op.execute("ALTER TABLE market_evidence DROP COLUMN IF EXISTS historical_accuracy")
    op.execute("ALTER TABLE market_evidence DROP COLUMN IF EXISTS historical_losses")
    op.execute("ALTER TABLE market_evidence DROP COLUMN IF EXISTS historical_wins")
    op.execute("ALTER TABLE market_evidence DROP COLUMN IF EXISTS historical_settled")