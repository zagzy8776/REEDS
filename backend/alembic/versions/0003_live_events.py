"""Live match events, lineups, push subscriptions

Revision ID: 0003_live_events
Revises: 0002_user_predictions
Create Date: 2026-06-21
"""

from alembic import op
import sqlalchemy as sa

revision = "0003_live_events"
down_revision = "0002_user_predictions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "match_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fixture_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("minute", sa.Integer(), nullable=True),
        sa.Column("team", sa.String(120), nullable=True),
        sa.Column("player", sa.String(120), nullable=True),
        sa.Column("assist", sa.String(120), nullable=True),
        sa.Column("detail", sa.String(120), nullable=True),
        sa.Column("home_score_at", sa.Integer(), nullable=True),
        sa.Column("away_score_at", sa.Integer(), nullable=True),
        sa.Column("extra", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "fixture_id", "event_type", "minute", "team", "player",
            name="uq_match_event",
        ),
    )
    op.create_index("ix_match_events_fixture_id", "match_events", ["fixture_id"])
    op.create_index("ix_match_events_event_type", "match_events", ["event_type"])
    op.create_index("ix_match_events_created_at", "match_events", ["created_at"])

    op.create_table(
        "match_lineups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fixture_id", sa.Integer(), nullable=False),
        sa.Column("team", sa.String(120), nullable=False),
        sa.Column("player", sa.String(120), nullable=False),
        sa.Column("position", sa.String(40), nullable=True),
        sa.Column("number", sa.Integer(), nullable=True),
        sa.Column("is_starter", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("formation", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("fixture_id", "team", "player", name="uq_lineup_player"),
    )
    op.create_index("ix_match_lineups_fixture_id", "match_lineups", ["fixture_id"])
    op.create_index("ix_match_lineups_team", "match_lineups", ["team"])

    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("endpoint", sa.Text(), nullable=False, unique=True),
        sa.Column("keys_p256dh", sa.Text(), nullable=True),
        sa.Column("keys_auth", sa.Text(), nullable=True),
        sa.Column("username", sa.String(80), nullable=True),
        sa.Column("fixture_ids", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_push_subscriptions_username", "push_subscriptions", ["username"])


def downgrade() -> None:
    op.drop_index("ix_push_subscriptions_username", table_name="push_subscriptions")
    op.drop_table("push_subscriptions")
    op.drop_index("ix_match_lineups_team", table_name="match_lineups")
    op.drop_index("ix_match_lineups_fixture_id", table_name="match_lineups")
    op.drop_table("match_lineups")
    op.drop_index("ix_match_events_created_at", table_name="match_events")
    op.drop_index("ix_match_events_event_type", table_name="match_events")
    op.drop_index("ix_match_events_fixture_id", table_name="match_events")
    op.drop_table("match_events")
