"""baseline: CockroachDB — every table owned by the 'cockroach' role

CockroachDB is intentionally NOT assumed identical to PostgreSQL: unique
constraints and index DDL are emitted by SQLAlchemy's Cockroach dialect at
apply time. fixtures_archive has no ORM model; it is a structural clone of
the fixtures table for cold (pre-730d) rows. backtest_runs and
historical_evaluation are generated from Base.metadata.

Revision ID: crdb_baseline_0001
Revises:
Create Date: 2026-09-12
"""

from alembic import op
import sqlalchemy as sa

revision = "crdb_baseline_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fixtures_archive",
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('sport', sa.String(length=30), nullable=False),
        sa.Column('league', sa.String(length=80), nullable=False),
        sa.Column('season', sa.String(length=20), nullable=False),
        sa.Column('match_date', sa.Date(), nullable=False),
        sa.Column('home_team', sa.String(length=120), nullable=False),
        sa.Column('away_team', sa.String(length=120), nullable=False),
        sa.Column('home_score', sa.Integer()),
        sa.Column('away_score', sa.Integer()),
        sa.Column('home_odds', sa.Float()),
        sa.Column('draw_odds', sa.Float()),
        sa.Column('away_odds', sa.Float()),
        sa.Column('source', sa.String(length=80), nullable=False),
        sa.Column('extra', sa.JSON()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('sport', 'league', 'match_date', 'home_team', 'away_team', name='uq_fixture'),
    )
    op.create_index('ix_fixtures_away_team', "fixtures_archive", ['away_team'])
    op.create_index('ix_fixtures_home_team', "fixtures_archive", ['home_team'])
    op.create_index('ix_fixtures_league', "fixtures_archive", ['league'])
    op.create_index('ix_fixtures_match_date', "fixtures_archive", ['match_date'])
    op.create_index('ix_fixtures_season', "fixtures_archive", ['season'])
    op.create_index('ix_fixtures_sport', "fixtures_archive", ['sport'])
    op.create_table(
        'historical_evaluation',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('fixture_id', sa.Integer(), nullable=False),
        sa.Column('sport', sa.String(length=30), nullable=False),
        sa.Column('league', sa.String(length=80)),
        sa.Column('match_date', sa.Date(), nullable=False),
        sa.Column('home_team', sa.String(length=120), nullable=False),
        sa.Column('away_team', sa.String(length=120), nullable=False),
        sa.Column('home_score', sa.Integer()),
        sa.Column('away_score', sa.Integer()),
        sa.Column('market', sa.String(length=50), nullable=False),
        sa.Column('pick', sa.String(length=120), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=False),
        sa.Column('edge_score', sa.Float(), nullable=False),
        sa.Column('outcome', sa.String(length=10)),
        sa.Column('brier_score', sa.Float()),
        sa.Column('has_odds', sa.Boolean(), nullable=False),
        sa.Column('applied_odds', sa.Float()),
        sa.Column('roi_units', sa.Float()),
        sa.Column('model_version_id', sa.Integer()),
        sa.Column('inference_mode', sa.String(length=40), nullable=False),
        sa.Column('fold_index', sa.Integer(), nullable=False),
        sa.Column('job_id', sa.String(length=80)),
        sa.Column('source', sa.String(length=80), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('fixture_id', 'market', 'model_version_id', 'fold_index', name='uq_historical_eval'),
    )
    op.create_index('ix_historical_evaluation_created_at', 'historical_evaluation', ['created_at'])
    op.create_index('ix_historical_evaluation_fixture_id', 'historical_evaluation', ['fixture_id'])
    op.create_index('ix_historical_evaluation_market', 'historical_evaluation', ['market'])
    op.create_index('ix_historical_evaluation_match_date', 'historical_evaluation', ['match_date'])
    op.create_index('ix_historical_evaluation_model_version_id', 'historical_evaluation', ['model_version_id'])
    op.create_index('ix_historical_evaluation_sport', 'historical_evaluation', ['sport'])
    op.create_table(
        'backtest_runs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('sport', sa.String(length=30), nullable=False),
        sa.Column('model_type', sa.String(length=50), nullable=False),
        sa.Column('split_strategy', sa.String(length=80), nullable=False),
        sa.Column('sample_size', sa.Integer(), nullable=False),
        sa.Column('accuracy', sa.Float(), nullable=False),
        sa.Column('brier_score', sa.Float()),
        sa.Column('log_loss', sa.Float()),
        sa.Column('roi_estimate', sa.Float()),
        sa.Column('metrics', sa.JSON()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_backtest_runs_created_at', 'backtest_runs', ['created_at'])
    op.create_index('ix_backtest_runs_sport', 'backtest_runs', ['sport'])


def downgrade() -> None:
    op.drop_table('backtest_runs')
    op.drop_table('historical_evaluation')
    op.drop_table('fixtures_archive')
