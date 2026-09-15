import logging
import uuid
from datetime import date, datetime, timedelta
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Standing, FixtureStat, TeamPerformance, DataSourceProvenance
from app.scraper.providers import (
    SportsDataProvider,
    StandingsProvider,
    StandingsRow,
    FixtureInfo,
    MatchStats,
    TeamStats,
    get_provider_chain,
)
from app.scraper.flashscore_client import FlashscoreProvider
from app.scraper.football_data_provider import FootballDataCsvProvider
from app.scraper.afriscores_provider import AfriScoresProvider
from app.scraper.espn_provider import ESPNProvider
from app.utils.team_names import normalize_team_name

log = logging.getLogger(__name__)


class StandingsIngestor:
    """Orchestrates multiple data providers and persists standings to the DB.

    Core responsibilities:
      - Fetch standings from configured providers (Flashscore, Football-Data CSV).
      - Canonicalize team names across providers.
      - Write ``Standing`` rows with ``effective_date`` strictly before fixture
        dates to prevent lookahead leakage in feature engineering.
      - Record provenance/conflicts in ``DataSourceProvenance`` when providers
        disagree on a team's position or points.
      - Generate ``TeamPerformance`` derived metrics (streaks, scoring rate).
    """

    def __init__(self, db: Session, providers: list[SportsDataProvider] | None = None):
        self.db = db
        if providers is None:
            self.providers: list[SportsDataProvider] = get_provider_chain()
        else:
            self.providers = list(providers)

    # ------------------------------------------------------------------
    # Standings ingestion
    # ------------------------------------------------------------------

    def ingest_standings(
        self,
        sport: str,
        league: str,
        season: str,
        target_date: date | str,
        providers: list[str] | None = None,
    ) -> int:
        """Ingest standings for a league as of ``target_date``.

        ``target_date`` is the latest fixture date that has concluded.
        Standings are stored with ``effective_date`` = the date the snapshot
        became known, so feature builders can safely query ``effective_date <
        fixture.match_date``.

        Returns the number of standing rows written.
        """
        if isinstance(target_date, str):
            target_date = datetime.strptime(target_date, "%Y-%m-%d").date()

        ingestion_id = str(uuid.uuid4())
        rows_written = 0

        active_providers = self._select_providers(providers)
        all_rows: list[StandingsRow] = []

        for provider in active_providers:
            if not self._provider_covers_league(provider, sport, league):
                continue
            try:
                rows = provider.get_standings(sport, league, season, target_date)
                all_rows.extend(rows)
            except Exception:
                log.exception("provider %s failed standings for %s/%s", provider.provider_name, league, season)

        if not all_rows:
            return 0

        rows_written = self._persist_standings(all_rows, league, season, target_date, ingestion_id)
        self._detect_and_record_conflicts(all_rows, league, season, target_date, ingestion_id)
        self._compute_team_performance(league, season, target_date)
        self.db.commit()

        log.info("ingested %d standing rows for %s/%s (ingestion=%s)", rows_written, league, season, ingestion_id)
        return rows_written

    def _select_providers(self, provider_names: list[str] | None) -> list[SportsDataProvider]:
        if not provider_names:
            return self.providers
        return [p for p in self.providers if p.provider_name in provider_names]

    def _provider_covers_league(self, provider: SportsDataProvider, sport: str, league: str) -> bool:
        supported = provider.get_supported_leagues(sport)
        if not supported:
            return True
        return any(
            s.lower().strip() == str(league).lower().strip() for s in supported
        )

    def _persist_standings(
        self,
        rows: Sequence[StandingsRow],
        league: str,
        season: str,
        effective_date: date,
        ingestion_id: str,
    ) -> int:
        """Upsert standings into the DB, deduplicating by (provider, team, date)."""
        written = 0

        for row in rows:
            team = normalize_team_name(row.team, row.sport)

            existing = self.db.execute(
                select(Standing).where(
                    Standing.sport == row.sport,
                    Standing.league == league,
                    Standing.season == season,
                    Standing.team == team,
                    Standing.provider == row.provider,
                    Standing.effective_date == effective_date,
                    Standing.standing_type == "total",
                )
            ).scalar_one_or_none()

            standing = existing or Standing(
                sport=row.sport,
                league=league,
                season=season,
                team=team,
                provider=row.provider,
                standing_type="total",
                effective_date=effective_date,
                ingestion_id=ingestion_id,
            )

            standing.position = row.position
            standing.points = row.points
            standing.games_played = row.games_played
            standing.wins = row.wins
            standing.draws = row.draws
            standing.losses = row.losses
            standing.goals_for = row.goals_for
            standing.goals_against = row.goals_against
            standing.goal_difference = row.goal_difference
            standing.recent_form = row.recent_form
            standing.ingestion_id = ingestion_id
            standing.created_at = datetime.utcnow()

            if existing is None:
                self.db.add(standing)
            written += 1

        # Write home/away splits if available
        for row in rows:
            if row.home_position is None and row.away_position is None:
                continue
            team = normalize_team_name(row.team, row.sport)
            self._persist_split(row, league, season, effective_date, ingestion_id, "home", row.home_position, row.home_points, row.home_games_played, row.home_wins, row.home_draws, row.home_losses, row.home_goals_for, row.home_goals_against)
            self._persist_split(row, league, season, effective_date, ingestion_id, "away", row.away_position, row.away_points, row.away_games_played, row.away_wins, row.away_draws, row.away_losses, row.away_goals_for, row.away_goals_against)

        return written

    def _persist_split(
        self,
        row: StandingsRow,
        league: str,
        season: str,
        effective_date: date,
        ingestion_id: str,
        standing_type: str,
        position, points, games_played, wins, draws, losses, gf, ga,
    ) -> None:
        if position is None:
            return
        team = normalize_team_name(row.team, row.sport)
        standing = Standing(
            sport=row.sport,
            league=league,
            season=season,
            team=team,
            provider=row.provider,
            standing_type=standing_type,
            position=position,
            points=points,
            games_played=games_played,
            wins=wins,
            draws=draws,
            losses=losses,
            goals_for=gf,
            goals_against=ga,
            goal_difference=(gf - ga) if (gf is not None and ga is not None) else None,
            effective_date=effective_date,
            ingestion_id=ingestion_id,
            created_at=datetime.utcnow(),
        )
        self.db.add(standing)

    def _upsert_provenance(
        self,
        entity_type: str,
        entity_key: str,
        sport: str,
        league: str | None,
        provider: str,
        provider_id: str | None,
        metric_name: str | None,
        value: float | None,
        confidence_score: float | None,
        status: str,
        effective_date: date,
        now: datetime,
    ) -> None:
        existing = self.db.execute(
            select(DataSourceProvenance).where(
                DataSourceProvenance.entity_type == entity_type,
                DataSourceProvenance.entity_key == entity_key,
                DataSourceProvenance.provider == provider,
                DataSourceProvenance.effective_date == effective_date,
            )
        ).scalar_one_or_none()

        if existing:
            existing.value = value
            existing.confidence_score = confidence_score
            existing.status = status
            existing.checked_at = now
        else:
            self.db.add(DataSourceProvenance(
                entity_type=entity_type,
                entity_key=entity_key,
                sport=sport,
                league=league,
                provider=provider,
                provider_id=provider_id,
                metric_name=metric_name,
                value=value,
                confidence_score=confidence_score,
                status=status,
                effective_date=effective_date,
                checked_at=now,
                created_at=now,
            ))

    # ------------------------------------------------------------------
    # Match stats ingestion
    # ------------------------------------------------------------------

    def ingest_match_stats(
        self,
        sport: str,
        home_team: str,
        away_team: str,
        match_date: date | str,
        providers: list[str] | None = None,
    ) -> MatchStats | None:
        """Fetch and persist match-level statistics for a fixture."""
        if isinstance(match_date, str):
            match_date = datetime.strptime(match_date, "%Y-%m-%d").date()

        home_canonical = normalize_team_name(home_team, sport)
        away_canonical = normalize_team_name(away_team, sport)

        ingestion_id = str(uuid.uuid4())
        active_providers = self._select_providers(providers)

        for provider in active_providers:
            try:
                stats = provider.get_match_stats(sport, home_team, away_team, match_date)
            except Exception:
                log.exception("provider %s failed match stats", provider.provider_name)
                continue

            if stats is None:
                continue

            self._persist_match_stats(stats, sport, match_date, ingestion_id)
            self.db.commit()
            return stats

        return None

    def _persist_match_stats(self, stats: MatchStats, sport: str, match_date: date, ingestion_id: str) -> None:
        for row in self._find_fixtures_for_stats(stats, sport, match_date):
            fixture_stat = FixtureStat(
                fixture_id=row.id,
                sport=sport,
                league=row.league,
                match_date=match_date,
                home_team=normalize_team_name(stats.home_team, sport),
                away_team=normalize_team_name(stats.away_team, sport),
                provider=stats.provider,
                provider_fixture_id=stats.provider_fixture_id,
                statistics=stats.statistics,
                effective_date=match_date,
                ingestion_id=ingestion_id,
            )
            self.db.add(fixture_stat)

    def _find_fixtures_for_stats(self, stats: MatchStats, sport: str, match_date: date) -> list:
        """Look up Fixture rows matching the match stats (by team names + date)."""
        from app.db.models import Fixture

        return self.db.execute(
            select(Fixture).where(
                Fixture.sport == sport,
                Fixture.match_date == match_date,
                Fixture.home_team == normalize_team_name(stats.home_team, sport),
                Fixture.away_team == normalize_team_name(stats.away_team, sport),
            )
        ).scalars().all()

    # ------------------------------------------------------------------
    # Conflict detection & provenance
    # ------------------------------------------------------------------

    def _detect_and_record_conflicts(
        self,
        rows: Sequence[StandingsRow],
        league: str,
        season: str,
        effective_date: date,
        ingestion_id: str,
    ) -> None:
        """When multiple providers report different position/points for the
        same team, record the conflict in ``DataSourceProvenance``."""
        by_team: dict[str, list[StandingsRow]] = {}
        for row in rows:
            team = normalize_team_name(row.team, row.sport)
            by_team.setdefault(team, []).append(row)

        now = datetime.utcnow()
        for team, team_rows in by_team.items():
            if len(team_rows) < 2:
                r = team_rows[0]
                self._upsert_provenance(
                    "standing", f"{league}|{season}|{team}", r.sport, league,
                    r.provider, r.team_source_id, "position",
                    float(r.position) if r.position is not None else None,
                    1.0, "consistent", effective_date, now,
                )
                continue

            positions = {r.provider: r.position for r in team_rows}
            if len(set(positions.values())) > 1:
                for r in team_rows:
                    self._upsert_provenance(
                        "standing", f"{league}|{season}|{team}", r.sport, league,
                        r.provider, r.team_source_id, "position",
                        float(r.position) if r.position is not None else None,
                        0.5, "conflict", effective_date, now,
                    )

    # ------------------------------------------------------------------
    # Derived metrics
    # ------------------------------------------------------------------

    def _compute_team_performance(self, league: str, season: str, effective_date: date) -> None:
        """Compute simple derived metrics: points per game, goal difference per game."""
        from collections import Counter

        standings = self.db.execute(
            select(Standing).where(
                Standing.league == league,
                Standing.season == season,
                Standing.effective_date == effective_date,
                Standing.provider == "football_data_csv",
                Standing.standing_type == "total",
            )
        ).scalars().all()

        if not standings:
            # Fall back to any provider
            standings = self.db.execute(
                select(Standing).where(
                    Standing.league == league,
                    Standing.season == season,
                    Standing.effective_date == effective_date,
                    Standing.standing_type == "total",
                )
            ).scalars().all()

        now = datetime.utcnow()
        provider = standings[0].provider if standings else "derived"

        for st in standings:
            if st.games_played and st.games_played > 0:
                ppg = st.points / st.games_played
                self._upsert_team_perf(st.sport, st.team, "points_per_game", ppg, st.games_played, effective_date, provider, now)

                if st.goal_difference is not None:
                    gdpg = st.goal_difference / st.games_played
                    self._upsert_team_perf(st.sport, st.team, "goal_diff_per_game", gdpg, st.games_played, effective_date, provider, now)

                if st.goals_for and st.goals_for > 0:
                    self._upsert_team_perf(st.sport, st.team, "goals_per_game", st.goals_for / st.games_played, st.games_played, effective_date, provider, now)

            # Recent form as a numeric win rate over last N matches
            if st.recent_form:
                form_chars = st.recent_form.replace("D", "").replace("L", "").replace("W", "")
                wins_in_form = st.recent_form.count("W")
                valid = len(st.recent_form)
                if valid > 0:
                    self._upsert_team_perf(st.sport, st.team, "recent_form_win_rate", wins_in_form / valid, valid, effective_date, provider, now)

    def _upsert_team_perf(
        self,
        sport: str,
        team: str,
        metric_name: str,
        metric_value: float,
        window_size: int | None,
        effective_date: date,
        provider: str,
        now: datetime,
    ) -> None:
        existing = self.db.execute(
            select(TeamPerformance).where(
                TeamPerformance.sport == sport,
                TeamPerformance.team == team,
                TeamPerformance.metric_name == metric_name,
                TeamPerformance.effective_date == effective_date,
                TeamPerformance.window_size == window_size,
                TeamPerformance.provider == provider,
            )
        ).scalar_one_or_none()

        if existing:
            existing.metric_value = metric_value
        else:
            self.db.add(TeamPerformance(
                sport=sport,
                team=team,
                metric_name=metric_name,
                metric_value=metric_value,
                window_size=window_size,
                effective_date=effective_date,
                provider=provider,
                created_at=now,
            ))

    # ------------------------------------------------------------------
    # Query helpers for feature engineering
    # ------------------------------------------------------------------

    def get_team_standing(
        self,
        sport: str,
        league: str,
        team: str,
        fixture_date: date | str,
        standing_type: str = "total",
    ) -> Standing | None:
        """Return the most recent standing for a team before ``fixture_date``.

        Uses ``effective_date < fixture_date`` to guarantee no lookahead leakage.
        """
        if isinstance(fixture_date, str):
            fixture_date = datetime.strptime(fixture_date, "%Y-%m-%d").date()

        team = normalize_team_name(team, sport)

        return self.db.execute(
            select(Standing)
            .where(
                Standing.sport == sport,
                Standing.league == league,
                Standing.team == team,
                Standing.standing_type == standing_type,
                Standing.effective_date < fixture_date,
            )
            .order_by(Standing.effective_date.desc())
            .limit(1)
        ).scalar_one_or_none()

    def get_fixture_stats(
        self,
        sport: str,
        league: str,
        home_team: str,
        away_team: str,
        match_date: date | str,
    ) -> FixtureStat | None:
        if isinstance(match_date, str):
            match_date = datetime.strptime(match_date, "%Y-%m-%d").date()

        return self.db.execute(
            select(FixtureStat).where(
                FixtureStat.sport == sport,
                FixtureStat.league == league,
                FixtureStat.home_team == normalize_team_name(home_team, sport),
                FixtureStat.away_team == normalize_team_name(away_team, sport),
                FixtureStat.match_date == match_date,
            )
        ).scalar_one_or_none()

    def bulk_ingest_standings(
        self,
        sport: str,
        league: str,
        season: str,
        start_date: date | str,
        end_date: date | str | None = None,
        providers: list[str] | None = None,
    ) -> int:
        """Ingest standings for every match date in a range.

        Each date gets a snapshot reflecting all results up to and including
        that date, so the feature pipeline can look up ``effective_date <
        fixture.match_date``.
        """
        if isinstance(start_date, str):
            start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
        if isinstance(end_date, str):
            end_date = datetime.strptime(end_date, "%Y-%m-%d").date()
        if end_date is None:
            end_date = date.today()

        total = 0
        current = start_date
        while current <= end_date:
            total += self.ingest_standings(sport, league, season, current, providers)
            current += timedelta(days=1)
        return total
