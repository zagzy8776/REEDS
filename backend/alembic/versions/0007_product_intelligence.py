"""model feedback and user follow tables for product intelligence

Revision ID: 0007_product_intelligence
Revises: 0006_historical_bootstrap
Create Date: 2026-09-10
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_product_intelligence"
down_revision = "0006_historical_bootstrap"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_feedback",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("prediction_id", sa.Integer(), nullable=False),
        sa.Column("fixture_id", sa.Integer(), nullable=False),
        sa.Column("sport", sa.String(length=30), nullable=False),
        sa.Column("league", sa.String(length=80), nullable=True),
        sa.Column("market", sa.String(length=50), nullable=False),
        sa.Column("pick", sa.String(length=120), nullable=False),
        sa.Column("predicted_probability", sa.Float(), nullable=False),
        sa.Column("actual_result", sa.String(length=10), nullable=False),
        sa.Column("probability_error", sa.Float(), nullable=False, server_default="0"),
        sa.Column("brier_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("final_score", sa.String(length=30), nullable=True),
        sa.Column("outcome_text", sa.String(length=160), nullable=True),
        sa.Column("error_type", sa.String(length=60), nullable=True),
        sa.Column("error_classifications", sa.JSON(), nullable=True),
        sa.Column("signal_attribution", sa.JSON(), nullable=True),
        sa.Column("defense_strong", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("feature_snapshot", sa.JSON(), nullable=True),
        sa.Column("successful_signals", sa.JSON(), nullable=True),
        sa.Column("failed_signals", sa.JSON(), nullable=True),
        sa.Column("contributing_factors", sa.JSON(), nullable=True),
        sa.Column("context", sa.JSON(), nullable=True),
        sa.Column("feedback_status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("validated_pattern", sa.JSON(), nullable=True),
        sa.Column("model_version", sa.Integer(), nullable=True),
        sa.Column("model_version_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("prediction_id", name="uq_model_feedback_prediction"),
    )
    op.create_index("ix_model_feedback_prediction_id", "model_feedback", ["prediction_id"])
    op.create_index("ix_model_feedback_fixture_id", "model_feedback", ["fixture_id"])
    op.create_index("ix_model_feedback_sport", "model_feedback", ["sport"])
    op.create_index("ix_model_feedback_market", "model_feedback", ["market"])
    op.create_index("ix_model_feedback_actual_result", "model_feedback", ["actual_result"])
    op.create_index("ix_model_feedback_error_type", "model_feedback", ["error_type"])
    op.create_index("ix_model_feedback_defense_strong", "model_feedback", ["defense_strong"])
    op.create_index("ix_model_feedback_feedback_status", "model_feedback", ["feedback_status"])
    op.create_index("ix_model_feedback_created_at", "model_feedback", ["created_at"])
    op.create_index("ix_model_feedback_updated_at", "model_feedback", ["updated_at"])

    op.create_table(
        "user_follows",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(length=80), nullable=False),
        sa.Column("entity_type", sa.String(length=20), nullable=False),
        sa.Column("entity_value", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("username", "entity_type", "entity_value", name="uq_user_follow"),
    )
    op.create_index("ix_user_follows_username", "user_follows", ["username"])
    op.create_index("ix_user_follows_entity_type", "user_follows", ["entity_type"])
    op.create_index("ix_user_follows_entity_value", "user_follows", ["entity_value"])


def downgrade() -> None:
    op.drop_table("user_follows")
    op.drop_table("model_feedback")