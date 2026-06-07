"""community user predictions

Revision ID: 0002_user_predictions
Revises: 0001_prediction_integrity
Create Date: 2026-06-07
"""

from alembic import op
import sqlalchemy as sa

revision = "0002_user_predictions"
down_revision = "0001_prediction_integrity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_predictions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fixture_id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=80), nullable=False),
        sa.Column("market", sa.String(length=50), nullable=False),
        sa.Column("pick", sa.String(length=120), nullable=False),
        sa.Column("analysis_text", sa.Text(), nullable=True),
        sa.Column("stake_units", sa.Float(), nullable=False, server_default="10"),
        sa.Column("is_settled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("was_correct", sa.Boolean(), nullable=True),
        sa.Column("profit_units", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("settled_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_user_predictions_fixture_id", "user_predictions", ["fixture_id"])
    op.create_index("ix_user_predictions_username", "user_predictions", ["username"])
    op.create_index("ix_user_predictions_market", "user_predictions", ["market"])
    op.create_index("ix_user_predictions_is_settled", "user_predictions", ["is_settled"])
    op.create_index("ix_user_predictions_created_at", "user_predictions", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_user_predictions_created_at", table_name="user_predictions")
    op.drop_index("ix_user_predictions_is_settled", table_name="user_predictions")
    op.drop_index("ix_user_predictions_market", table_name="user_predictions")
    op.drop_index("ix_user_predictions_username", table_name="user_predictions")
    op.drop_index("ix_user_predictions_fixture_id", table_name="user_predictions")
    op.drop_table("user_predictions")