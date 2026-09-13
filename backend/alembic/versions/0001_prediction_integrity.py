"""prediction integrity ledger and backtests

Revision ID: 0001_prediction_integrity
Revises:
Create Date: 2026-06-06
"""

from alembic import op
import sqlalchemy as sa

revision = "0001_prediction_integrity"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "teams",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sport", sa.String(length=30), nullable=False),
        sa.Column("canonical_name", sa.String(length=120), nullable=False),
        sa.Column("country", sa.String(length=80), nullable=True),
        sa.Column("league_hint", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("sport", "canonical_name", name="uq_team_canonical"),
    )
    op.create_index("ix_teams_sport", "teams", ["sport"])
    op.create_index("ix_teams_canonical_name", "teams", ["canonical_name"])

    op.create_table(
        "team_aliases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("sport", sa.String(length=30), nullable=False),
        sa.Column("alias", sa.String(length=120), nullable=False),
        sa.Column("alias_key", sa.String(length=160), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=False, server_default="manual"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("sport", "alias_key", name="uq_team_alias_key"),
    )
    op.create_index("ix_team_aliases_team_id", "team_aliases", ["team_id"])
    op.create_index("ix_team_aliases_sport", "team_aliases", ["sport"])
    op.create_index("ix_team_aliases_alias", "team_aliases", ["alias"])
    op.create_index("ix_team_aliases_alias_key", "team_aliases", ["alias_key"])

    with op.batch_alter_table("predictions") as batch_op:
        batch_op.add_column(sa.Column("model_version_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
        batch_op.add_column(sa.Column("status", sa.String(length=30), nullable=False, server_default="active"))
        batch_op.add_column(sa.Column("published_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("superseded_at", sa.DateTime(), nullable=True))
        batch_op.create_index("ix_predictions_model_version_id", ["model_version_id"])
        batch_op.create_index("ix_predictions_status", ["status"])

    op.create_table(
        "backtest_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sport", sa.String(length=30), nullable=False),
        sa.Column("model_type", sa.String(length=50), nullable=False),
        sa.Column("split_strategy", sa.String(length=80), nullable=False, server_default="walk_forward"),
        sa.Column("sample_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("accuracy", sa.Float(), nullable=False, server_default="0"),
        sa.Column("brier_score", sa.Float(), nullable=True),
        sa.Column("log_loss", sa.Float(), nullable=True),
        sa.Column("roi_estimate", sa.Float(), nullable=True),
        sa.Column("metrics", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_backtest_runs_sport", "backtest_runs", ["sport"])
    op.create_index("ix_backtest_runs_created_at", "backtest_runs", ["created_at"])

    op.create_table(
        "odds_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fixture_id", sa.Integer(), nullable=False),
        sa.Column("prediction_id", sa.Integer(), nullable=True),
        sa.Column("phase", sa.String(length=30), nullable=False),
        sa.Column("market", sa.String(length=50), nullable=False),
        sa.Column("bookmaker", sa.String(length=80), nullable=True),
        sa.Column("home_odds", sa.Float(), nullable=True),
        sa.Column("draw_odds", sa.Float(), nullable=True),
        sa.Column("away_odds", sa.Float(), nullable=True),
        sa.Column("line", sa.Float(), nullable=True),
        sa.Column("over_odds", sa.Float(), nullable=True),
        sa.Column("under_odds", sa.Float(), nullable=True),
        sa.Column("source", sa.String(length=80), nullable=False, server_default="fixture"),
        sa.Column("captured_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_odds_snapshots_fixture_id", "odds_snapshots", ["fixture_id"])
    op.create_index("ix_odds_snapshots_prediction_id", "odds_snapshots", ["prediction_id"])
    op.create_index("ix_odds_snapshots_phase", "odds_snapshots", ["phase"])
    op.create_index("ix_odds_snapshots_market", "odds_snapshots", ["market"])
    op.create_index("ix_odds_snapshots_captured_at", "odds_snapshots", ["captured_at"])


def downgrade() -> None:
    op.drop_index("ix_odds_snapshots_captured_at", table_name="odds_snapshots")
    op.drop_index("ix_odds_snapshots_market", table_name="odds_snapshots")
    op.drop_index("ix_odds_snapshots_phase", table_name="odds_snapshots")
    op.drop_index("ix_odds_snapshots_prediction_id", table_name="odds_snapshots")
    op.drop_index("ix_odds_snapshots_fixture_id", table_name="odds_snapshots")
    op.drop_table("odds_snapshots")
    op.drop_index("ix_backtest_runs_created_at", table_name="backtest_runs")
    op.drop_index("ix_backtest_runs_sport", table_name="backtest_runs")
    op.drop_table("backtest_runs")
    with op.batch_alter_table("predictions") as batch_op:
        batch_op.drop_index("ix_predictions_status")
        batch_op.drop_index("ix_predictions_model_version_id")
        batch_op.drop_column("superseded_at")
        batch_op.drop_column("published_at")
        batch_op.drop_column("status")
        batch_op.drop_column("version")
        batch_op.drop_column("model_version_id")
    op.drop_index("ix_team_aliases_alias_key", table_name="team_aliases")
    op.drop_index("ix_team_aliases_alias", table_name="team_aliases")
    op.drop_index("ix_team_aliases_sport", table_name="team_aliases")
    op.drop_index("ix_team_aliases_team_id", table_name="team_aliases")
    op.drop_table("team_aliases")
    op.drop_index("ix_teams_canonical_name", table_name="teams")
    op.drop_index("ix_teams_sport", table_name="teams")
    op.drop_table("teams")