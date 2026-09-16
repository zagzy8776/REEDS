"""standings, fixture_stats, team_performance, data_provenance tables

Revision ID: 0008_standings_tables
Revises: 0007_product_intelligence
Create Date: 2026-09-15
"""
from alembic import op
import sqlalchemy as sa

revision = "0008_standings_tables"
down_revision = "0007_product_intelligence"
branch_labels = None
depends_on = None


def _ensure_baseline_compat() -> None:
    """Handle production DBs whose alembic_version was created outside Alembic.

    Production Aiven carries ``aiven_baseline_0001`` which matches no
    revision file, so a plain ``alembic upgrade head`` fails with
    "Can't locate revision". If we detect such an unknown version stamp,
    point it at our parent revision (0007) first — the pre-0008 schema is
    already what's live, so no data-table changes are involved.
    Idempotent: a no-op when the stamp is already a known revision.
    """
    from alembic.migration import MigrationContext

    conn = op.get_bind()
    ctx = MigrationContext.configure(conn)
    current = ctx.get_current_heads()
    known = {"0001_prediction_integrity", "0002_user_predictions", "0003_live_events",
             "0004_insider_signals", "0005_market_evidence", "0006_historical_bootstrap",
             "0007_product_intelligence", "0008_standings_tables"}
    if current and not (set(current) <= known):
        op.execute("UPDATE alembic_version SET version_num='0007_product_intelligence'")


def upgrade() -> None:
    _ensure_baseline_compat()
    op.create_table(
        "standings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sport", sa.String(length=30), nullable=False, index=True),
        sa.Column("league", sa.String(length=80), nullable=False, index=True),
        sa.Column("season", sa.String(length=20), nullable=False),
        sa.Column("team", sa.String(length=120), nullable=False, index=True),
        sa.Column("standing_type", sa.String(length=20), nullable=False, server_default="total"),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.Column("points", sa.Integer(), nullable=True),
        sa.Column("games_played", sa.Integer(), nullable=True),
        sa.Column("wins", sa.Integer(), nullable=True),
        sa.Column("draws", sa.Integer(), nullable=True),
        sa.Column("losses", sa.Integer(), nullable=True),
        sa.Column("goals_for", sa.Integer(), nullable=True),
        sa.Column("goals_against", sa.Integer(), nullable=True),
        sa.Column("goal_difference", sa.Integer(), nullable=True),
        sa.Column("recent_form", sa.String(length=20), nullable=True),
        sa.Column("provider", sa.String(length=80), nullable=False, index=True),
        sa.Column("provider_team_id", sa.String(length=80), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=False, index=True),
        sa.Column("ingestion_id", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("sport", "league", "season", "team", "effective_date", "standing_type", name="uq_standing"),
    )
    op.create_index("ix_standings_created_at", "standings", ["created_at"])
    op.create_index("ix_standings_ingestion_id", "standings", ["ingestion_id"])

    op.create_table(
        "fixture_stats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fixture_id", sa.Integer(), nullable=True, index=True),
        sa.Column("sport", sa.String(length=30), nullable=False, index=True),
        sa.Column("league", sa.String(length=80), nullable=False, index=True),
        sa.Column("match_date", sa.Date(), nullable=False, index=True),
        sa.Column("home_team", sa.String(length=120), nullable=False),
        sa.Column("away_team", sa.String(length=120), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False, index=True),
        sa.Column("provider_fixture_id", sa.String(length=120), nullable=True),
        sa.Column("statistics", sa.JSON(), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=False, index=True),
        sa.Column("ingestion_id", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("fixture_id", "provider", "effective_date", name="uq_fixture_stat"),
    )
    op.create_index("ix_fixture_stats_created_at", "fixture_stats", ["created_at"])
    op.create_index("ix_fixture_stats_ingestion_id", "fixture_stats", ["ingestion_id"])

    op.create_table(
        "team_performance",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sport", sa.String(length=30), nullable=False, index=True),
        sa.Column("team", sa.String(length=120), nullable=False, index=True),
        sa.Column("metric_name", sa.String(length=80), nullable=False),
        sa.Column("metric_value", sa.Float(), nullable=False),
        sa.Column("window_size", sa.Integer(), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=False, index=True),
        sa.Column("provider", sa.String(length=80), nullable=False, server_default="derived"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("sport", "team", "metric_name", "effective_date", "window_size", "provider", name="uq_team_perf"),
    )
    op.create_index("ix_team_perf_created_at", "team_performance", ["created_at"])

    op.create_table(
        "data_provenance",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("entity_key", sa.String(length=255), nullable=False),
        sa.Column("sport", sa.String(length=30), nullable=False, index=True),
        sa.Column("league", sa.String(length=80), nullable=True),
        sa.Column("provider", sa.String(length=80), nullable=False, index=True),
        sa.Column("provider_id", sa.String(length=120), nullable=True),
        sa.Column("metric_name", sa.String(length=80), nullable=True),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False, index=True),
        sa.Column("checked_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("entity_type", "entity_key", "provider", "effective_date", name="uq_source_provenance"),
    )
    op.create_index("ix_data_prov_created_at", "data_provenance", ["created_at"])
    op.create_index("ix_data_prov_checked_at", "data_provenance", ["checked_at"])
    op.create_index("ix_data_prov_entity_type", "data_provenance", ["entity_type"])


def downgrade() -> None:
    op.drop_index("ix_data_prov_entity_type", table_name="data_provenance")
    op.drop_index("ix_data_prov_checked_at", table_name="data_provenance")
    op.drop_index("ix_data_prov_created_at", table_name="data_provenance")
    op.drop_table("data_provenance")
    op.drop_index("ix_team_perf_created_at", table_name="team_performance")
    op.drop_table("team_performance")
    op.drop_index("ix_fixture_stats_ingestion_id", table_name="fixture_stats")
    op.drop_index("ix_fixture_stats_created_at", table_name="fixture_stats")
    op.drop_table("fixture_stats")
    op.drop_index("ix_standings_ingestion_id", table_name="standings")
    op.drop_index("ix_standings_created_at", table_name="standings")
    op.drop_table("standings")