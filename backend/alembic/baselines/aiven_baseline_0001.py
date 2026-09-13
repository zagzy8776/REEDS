"""baseline: Aiven PostgreSQL — every table owned by the 'aiven' role

Generated from Base.metadata (models.py + rejected_fixture.py) so column
types, nullability, uniques, and index names match what create_all produced
on the legacy single-database deployment. Fixtures columns match the ORM
Fixture model; the hot/cold 730-day split is a row-level concern handled by
the migration script, not a schema difference.

Revision ID: aiven_baseline_0001
Revises:
Create Date: 2026-09-12
"""

from alembic import op
import sqlalchemy as sa

revision = "aiven_baseline_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'fixtures',
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
    op.create_index('ix_fixtures_away_team', 'fixtures', ['away_team'])
    op.create_index('ix_fixtures_home_team', 'fixtures', ['home_team'])
    op.create_index('ix_fixtures_league', 'fixtures', ['league'])
    op.create_index('ix_fixtures_match_date', 'fixtures', ['match_date'])
    op.create_index('ix_fixtures_season', 'fixtures', ['season'])
    op.create_index('ix_fixtures_sport', 'fixtures', ['sport'])
    op.create_table(
        'teams',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('sport', sa.String(length=30), nullable=False),
        sa.Column('canonical_name', sa.String(length=120), nullable=False),
        sa.Column('country', sa.String(length=80)),
        sa.Column('league_hint', sa.String(length=80)),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('sport', 'canonical_name', name='uq_team_canonical'),
    )
    op.create_index('ix_teams_canonical_name', 'teams', ['canonical_name'])
    op.create_index('ix_teams_sport', 'teams', ['sport'])
    op.create_table(
        'team_aliases',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('team_id', sa.Integer(), nullable=False),
        sa.Column('sport', sa.String(length=30), nullable=False),
        sa.Column('alias', sa.String(length=120), nullable=False),
        sa.Column('alias_key', sa.String(length=160), nullable=False),
        sa.Column('source', sa.String(length=80), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('sport', 'alias_key', name='uq_team_alias_key'),
    )
    op.create_index('ix_team_aliases_alias', 'team_aliases', ['alias'])
    op.create_index('ix_team_aliases_alias_key', 'team_aliases', ['alias_key'])
    op.create_index('ix_team_aliases_sport', 'team_aliases', ['sport'])
    op.create_index('ix_team_aliases_team_id', 'team_aliases', ['team_id'])
    op.create_table(
        'model_versions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('sport', sa.String(length=30), nullable=False),
        sa.Column('model_type', sa.String(length=120), nullable=False),
        sa.Column('path', sa.String(length=255), nullable=False),
        sa.Column('accuracy', sa.Float(), nullable=False),
        sa.Column('sample_size', sa.Integer(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('trained_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_model_versions_sport', 'model_versions', ['sport'])
    op.create_table(
        'model_artifacts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('sport', sa.String(length=30), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('model_type', sa.String(length=120), nullable=False),
        sa.Column('accuracy', sa.Float(), nullable=False),
        sa.Column('sample_size', sa.Integer(), nullable=False),
        sa.Column('data', sa.LargeBinary(), nullable=False),
        sa.Column('metadata_json', sa.JSON()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('sport', 'filename', name='uq_model_artifact'),
    )
    op.create_index('ix_model_artifacts_created_at', 'model_artifacts', ['created_at'])
    op.create_index('ix_model_artifacts_sport', 'model_artifacts', ['sport'])
    op.create_table(
        'predictions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('fixture_id', sa.Integer(), nullable=False),
        sa.Column('model_version_id', sa.Integer()),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=30), nullable=False),
        sa.Column('market', sa.String(length=50), nullable=False),
        sa.Column('pick', sa.String(length=120), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=False),
        sa.Column('edge_score', sa.Float(), nullable=False),
        sa.Column('risk_level', sa.String(length=30), nullable=False),
        sa.Column('reasoning', sa.Text(), nullable=False),
        sa.Column('is_premium', sa.Boolean(), nullable=False),
        sa.Column('is_published', sa.Boolean(), nullable=False),
        sa.Column('engine_meta', sa.JSON()),
        sa.Column('published_at', sa.DateTime()),
        sa.Column('superseded_at', sa.DateTime()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_predictions_created_at', 'predictions', ['created_at'])
    op.create_index('ix_predictions_fixture_id', 'predictions', ['fixture_id'])
    op.create_index('ix_predictions_market', 'predictions', ['market'])
    op.create_index('ix_predictions_model_version_id', 'predictions', ['model_version_id'])
    op.create_index('ix_predictions_status', 'predictions', ['status'])
    op.create_table(
        'odds_snapshots',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('fixture_id', sa.Integer(), nullable=False),
        sa.Column('prediction_id', sa.Integer()),
        sa.Column('phase', sa.String(length=30), nullable=False),
        sa.Column('market', sa.String(length=50), nullable=False),
        sa.Column('bookmaker', sa.String(length=80)),
        sa.Column('home_odds', sa.Float()),
        sa.Column('draw_odds', sa.Float()),
        sa.Column('away_odds', sa.Float()),
        sa.Column('line', sa.Float()),
        sa.Column('over_odds', sa.Float()),
        sa.Column('under_odds', sa.Float()),
        sa.Column('source', sa.String(length=80), nullable=False),
        sa.Column('captured_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_odds_snapshots_captured_at', 'odds_snapshots', ['captured_at'])
    op.create_index('ix_odds_snapshots_fixture_id', 'odds_snapshots', ['fixture_id'])
    op.create_index('ix_odds_snapshots_market', 'odds_snapshots', ['market'])
    op.create_index('ix_odds_snapshots_phase', 'odds_snapshots', ['phase'])
    op.create_index('ix_odds_snapshots_prediction_id', 'odds_snapshots', ['prediction_id'])
    op.create_table(
        'market_evidence',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('sport', sa.String(length=30), nullable=False),
        sa.Column('market', sa.String(length=50), nullable=False),
        sa.Column('settled', sa.Integer(), nullable=False),
        sa.Column('wins', sa.Integer(), nullable=False),
        sa.Column('losses', sa.Integer(), nullable=False),
        sa.Column('pushes', sa.Integer(), nullable=False),
        sa.Column('recent_settled', sa.Integer(), nullable=False),
        sa.Column('recent_wins', sa.Integer(), nullable=False),
        sa.Column('recent_losses', sa.Integer(), nullable=False),
        sa.Column('accuracy', sa.Float()),
        sa.Column('recent_accuracy', sa.Float()),
        sa.Column('brier_score', sa.Float()),
        sa.Column('expected_value', sa.Float()),
        sa.Column('roi_units', sa.Float()),
        sa.Column('last_loss_streak', sa.Integer(), nullable=False),
        sa.Column('publication_blocked', sa.Boolean(), nullable=False),
        sa.Column('block_reasons', sa.JSON()),
        sa.Column('is_model_trained', sa.Boolean(), nullable=False),
        sa.Column('historical_settled', sa.Integer(), nullable=False),
        sa.Column('historical_wins', sa.Integer(), nullable=False),
        sa.Column('historical_losses', sa.Integer(), nullable=False),
        sa.Column('historical_accuracy', sa.Float()),
        sa.Column('historical_brier_sum', sa.Float()),
        sa.Column('historical_brier_count', sa.Integer(), nullable=False),
        sa.Column('historical_odds_count', sa.Integer(), nullable=False),
        sa.Column('historical_roi_units', sa.Float()),
        sa.Column('historical_has_odds', sa.Boolean(), nullable=False),
        sa.Column('bootstrap_updated_at', sa.DateTime()),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('sport', 'market', name='uq_market_evidence'),
    )
    op.create_index('ix_market_evidence_market', 'market_evidence', ['market'])
    op.create_index('ix_market_evidence_sport', 'market_evidence', ['sport'])
    op.create_index('ix_market_evidence_updated_at', 'market_evidence', ['updated_at'])
    op.create_table(
        'model_feedback',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('prediction_id', sa.Integer(), nullable=False, unique=True),
        sa.Column('fixture_id', sa.Integer(), nullable=False),
        sa.Column('sport', sa.String(length=30), nullable=False),
        sa.Column('league', sa.String(length=80)),
        sa.Column('market', sa.String(length=50), nullable=False),
        sa.Column('pick', sa.String(length=120), nullable=False),
        sa.Column('predicted_probability', sa.Float(), nullable=False),
        sa.Column('actual_result', sa.String(length=10), nullable=False),
        sa.Column('probability_error', sa.Float(), nullable=False),
        sa.Column('brier_score', sa.Float(), nullable=False),
        sa.Column('final_score', sa.String(length=30)),
        sa.Column('outcome_text', sa.String(length=160)),
        sa.Column('error_type', sa.String(length=60)),
        sa.Column('error_classifications', sa.JSON()),
        sa.Column('signal_attribution', sa.JSON()),
        sa.Column('defense_strong', sa.Boolean(), nullable=False),
        sa.Column('feature_snapshot', sa.JSON()),
        sa.Column('successful_signals', sa.JSON()),
        sa.Column('failed_signals', sa.JSON()),
        sa.Column('contributing_factors', sa.JSON()),
        sa.Column('context', sa.JSON()),
        sa.Column('feedback_status', sa.String(length=20), nullable=False),
        sa.Column('validated_pattern', sa.JSON()),
        sa.Column('model_version', sa.Integer()),
        sa.Column('model_version_id', sa.Integer()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_model_feedback_actual_result', 'model_feedback', ['actual_result'])
    op.create_index('ix_model_feedback_created_at', 'model_feedback', ['created_at'])
    op.create_index('ix_model_feedback_defense_strong', 'model_feedback', ['defense_strong'])
    op.create_index('ix_model_feedback_error_type', 'model_feedback', ['error_type'])
    op.create_index('ix_model_feedback_feedback_status', 'model_feedback', ['feedback_status'])
    op.create_index('ix_model_feedback_fixture_id', 'model_feedback', ['fixture_id'])
    op.create_index('ix_model_feedback_market', 'model_feedback', ['market'])
    op.create_index('ix_model_feedback_prediction_id', 'model_feedback', ['prediction_id'], unique=True)
    op.create_index('ix_model_feedback_sport', 'model_feedback', ['sport'])
    op.create_index('ix_model_feedback_updated_at', 'model_feedback', ['updated_at'])
    op.create_table(
        'match_events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('fixture_id', sa.Integer(), nullable=False),
        sa.Column('event_type', sa.String(length=40), nullable=False),
        sa.Column('minute', sa.Integer()),
        sa.Column('team', sa.String(length=120)),
        sa.Column('player', sa.String(length=120)),
        sa.Column('assist', sa.String(length=120)),
        sa.Column('detail', sa.String(length=120)),
        sa.Column('home_score_at', sa.Integer()),
        sa.Column('away_score_at', sa.Integer()),
        sa.Column('extra', sa.JSON()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('fixture_id', 'event_type', 'minute', 'team', 'player', name='uq_match_event'),
    )
    op.create_index('ix_match_events_created_at', 'match_events', ['created_at'])
    op.create_index('ix_match_events_event_type', 'match_events', ['event_type'])
    op.create_index('ix_match_events_fixture_id', 'match_events', ['fixture_id'])
    op.create_table(
        'match_lineups',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('fixture_id', sa.Integer(), nullable=False),
        sa.Column('team', sa.String(length=120), nullable=False),
        sa.Column('player', sa.String(length=120), nullable=False),
        sa.Column('position', sa.String(length=40)),
        sa.Column('number', sa.Integer()),
        sa.Column('is_starter', sa.Boolean(), nullable=False),
        sa.Column('formation', sa.String(length=20)),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('fixture_id', 'team', 'player', name='uq_lineup_player'),
    )
    op.create_index('ix_match_lineups_fixture_id', 'match_lineups', ['fixture_id'])
    op.create_index('ix_match_lineups_team', 'match_lineups', ['team'])
    op.create_table(
        'insider_signals',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('fixture_id', sa.Integer(), nullable=False),
        sa.Column('sport', sa.String(length=30), nullable=False),
        sa.Column('signal_type', sa.String(length=40), nullable=False),
        sa.Column('value', sa.Float()),
        sa.Column('direction', sa.String(length=20)),
        sa.Column('description', sa.String(length=255)),
        sa.Column('source', sa.String(length=80), nullable=False),
        sa.Column('captured_at', sa.DateTime(), nullable=False),
        sa.Column('extra', sa.JSON()),
        sa.UniqueConstraint('fixture_id', 'signal_type', 'source', name='uq_insider_signal'),
    )
    op.create_index('ix_insider_signals_captured_at', 'insider_signals', ['captured_at'])
    op.create_index('ix_insider_signals_fixture_id', 'insider_signals', ['fixture_id'])
    op.create_index('ix_insider_signals_signal_type', 'insider_signals', ['signal_type'])
    op.create_index('ix_insider_signals_sport', 'insider_signals', ['sport'])
    op.create_table(
        'user_predictions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('fixture_id', sa.Integer(), nullable=False),
        sa.Column('username', sa.String(length=80), nullable=False),
        sa.Column('market', sa.String(length=50), nullable=False),
        sa.Column('pick', sa.String(length=120), nullable=False),
        sa.Column('analysis_text', sa.Text()),
        sa.Column('stake_units', sa.Float(), nullable=False),
        sa.Column('is_settled', sa.Boolean(), nullable=False),
        sa.Column('was_correct', sa.Boolean()),
        sa.Column('profit_units', sa.Float()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('settled_at', sa.DateTime()),
    )
    op.create_index('ix_user_predictions_created_at', 'user_predictions', ['created_at'])
    op.create_index('ix_user_predictions_fixture_id', 'user_predictions', ['fixture_id'])
    op.create_index('ix_user_predictions_is_settled', 'user_predictions', ['is_settled'])
    op.create_index('ix_user_predictions_market', 'user_predictions', ['market'])
    op.create_index('ix_user_predictions_username', 'user_predictions', ['username'])
    op.create_table(
        'community_comments',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('prediction_id', sa.Integer(), nullable=False),
        sa.Column('username', sa.String(length=80), nullable=False),
        sa.Column('comment_text', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_community_comments_created_at', 'community_comments', ['created_at'])
    op.create_index('ix_community_comments_prediction_id', 'community_comments', ['prediction_id'])
    op.create_index('ix_community_comments_username', 'community_comments', ['username'])
    op.create_table(
        'community_reactions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('prediction_id', sa.Integer(), nullable=False),
        sa.Column('username', sa.String(length=80), nullable=False),
        sa.Column('reaction', sa.String(length=30), nullable=False),
        sa.Column('rating', sa.Integer()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_community_reactions_prediction_id', 'community_reactions', ['prediction_id'])
    op.create_index('ix_community_reactions_reaction', 'community_reactions', ['reaction'])
    op.create_index('ix_community_reactions_username', 'community_reactions', ['username'])
    op.create_table(
        'community_plays',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('prediction_id', sa.Integer(), nullable=False),
        sa.Column('username', sa.String(length=80), nullable=False),
        sa.Column('stake_units', sa.Float(), nullable=False),
        sa.Column('status', sa.String(length=30), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_community_plays_created_at', 'community_plays', ['created_at'])
    op.create_index('ix_community_plays_prediction_id', 'community_plays', ['prediction_id'])
    op.create_index('ix_community_plays_status', 'community_plays', ['status'])
    op.create_index('ix_community_plays_username', 'community_plays', ['username'])
    op.create_table(
        'win_slips',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('prediction_id', sa.Integer()),
        sa.Column('username', sa.String(length=80), nullable=False),
        sa.Column('title', sa.String(length=160), nullable=False),
        sa.Column('proof_text', sa.Text()),
        sa.Column('profit_units', sa.Float()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_win_slips_prediction_id', 'win_slips', ['prediction_id'])
    op.create_index('ix_win_slips_username', 'win_slips', ['username'])
    op.create_table(
        'user_follows',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('username', sa.String(length=80), nullable=False),
        sa.Column('entity_type', sa.String(length=20), nullable=False),
        sa.Column('entity_value', sa.String(length=120), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('username', 'entity_type', 'entity_value', name='uq_user_follow'),
    )
    op.create_index('ix_user_follows_entity_type', 'user_follows', ['entity_type'])
    op.create_index('ix_user_follows_entity_value', 'user_follows', ['entity_value'])
    op.create_index('ix_user_follows_username', 'user_follows', ['username'])
    op.create_table(
        'user_subscriptions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('email', sa.String(length=180), nullable=False, unique=True),
        sa.Column('plan', sa.String(length=30), nullable=False),
        sa.Column('expires_at', sa.DateTime()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_user_subscriptions_email', 'user_subscriptions', ['email'], unique=True)
    op.create_table(
        'push_subscriptions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('endpoint', sa.Text(), nullable=False, unique=True),
        sa.Column('keys_p256dh', sa.Text()),
        sa.Column('keys_auth', sa.Text()),
        sa.Column('username', sa.String(length=80)),
        sa.Column('fixture_ids', sa.JSON()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('endpoint', name=None),
    )
    op.create_index('ix_push_subscriptions_username', 'push_subscriptions', ['username'])
    op.create_table(
        'rejected_fixtures',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('provider', sa.String(length=80), nullable=False),
        sa.Column('raw_home', sa.String(length=200), nullable=False),
        sa.Column('raw_away', sa.String(length=200), nullable=False),
        sa.Column('reason', sa.String(length=80), nullable=False),
        sa.Column('sport', sa.String(length=30)),
        sa.Column('league', sa.String(length=80)),
        sa.Column('match_date', sa.Date()),
        sa.Column('raw_payload', sa.JSON()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_rejected_fixtures_created_at', 'rejected_fixtures', ['created_at'])
    op.create_index('ix_rejected_fixtures_match_date', 'rejected_fixtures', ['match_date'])
    op.create_index('ix_rejected_fixtures_provider', 'rejected_fixtures', ['provider'])
    op.create_index('ix_rejected_fixtures_reason', 'rejected_fixtures', ['reason'])
    op.create_index('ix_rejected_fixtures_sport', 'rejected_fixtures', ['sport'])


def downgrade() -> None:
    op.drop_table('rejected_fixtures')
    op.drop_table('push_subscriptions')
    op.drop_table('user_subscriptions')
    op.drop_table('user_follows')
    op.drop_table('win_slips')
    op.drop_table('community_plays')
    op.drop_table('community_reactions')
    op.drop_table('community_comments')
    op.drop_table('user_predictions')
    op.drop_table('insider_signals')
    op.drop_table('match_lineups')
    op.drop_table('match_events')
    op.drop_table('model_feedback')
    op.drop_table('market_evidence')
    op.drop_table('odds_snapshots')
    op.drop_table('predictions')
    op.drop_table('model_artifacts')
    op.drop_table('model_versions')
    op.drop_table('team_aliases')
    op.drop_table('teams')
    op.drop_table('fixtures')
