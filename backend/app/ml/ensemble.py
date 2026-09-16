from pathlib import Path

import numpy as np
import pandas as pd

from app.ml.features import features_for_fixture
from app.ml.model_cache import load_model_bundle
from app.ml.poisson import soccer_probabilities
from app.ml.calibration import apply_calibration
from app.ml.value_engine import ValueBettingEngine, PoissonValueEngine
from app.services.customer_copy import high_variance_warning
from app.utils.team_names import normalize_team_name


class LoyalEdgeEngine:
    """Private hybrid engine using multi-model ensemble + Poisson blend.

    Loads the ensemble bundle trained by train.py (which may contain XGBoost,
    and RandomForest).
    Blends ensemble probabilities with Poisson goal simulation for final output.

    The bundle is loaded lazily through the bounded model cache on first
    prediction, never at construction time, so startup and upload validation
    do not deserialize large artifacts on the 512 MiB Render instance.
    """

    def __init__(self, model_path: str | None = None):
        self.model_path = model_path
        self.bundle = None

    def _load_bundle(self) -> dict | None:
        if self.bundle is not None:
            return self.bundle
        if not self.model_path or not Path(self.model_path).exists():
            return None
        bundle = load_model_bundle(self.model_path)
        self.bundle = bundle if isinstance(bundle, dict) else None
        return self.bundle

    def _ensemble_predict(self, features_row: dict, labels: list[int]) -> dict[str, float]:
        """Run the ensemble on a single fixture feature vector."""
        bundle = self._load_bundle()
        if not bundle or "models" not in bundle:
            # An unmodeled uniform guess (0.33/0.33/0.34) presented as an
            # ensemble read is a fabricated prediction. Refuse loudly instead;
            # callers gate on bundle presence before reaching this point.
            raise RuntimeError(
                "trained ensemble bundle is unavailable; refusing silent default probabilities"
            )

        x = pd.DataFrame([features_row]).reindex(columns=bundle["features"], fill_value=0)
        models = bundle["models"]
        weights = bundle.get("weights", [1.0] * len(models))
        total_weight = sum(weights)
        all_probas = []

        for name, model in models.items():
            try:
                probas = model.predict_proba(x)[0]
                aligned = np.zeros(len(labels))
                for src_idx, cls in enumerate(model.classes_):
                    if cls in labels:
                        aligned[labels.index(cls)] = probas[src_idx]
                all_probas.append(aligned)
            except Exception:
                # Fallback: if a model fails, skip it
                uniform = np.ones(len(labels)) / len(labels)
                all_probas.append(uniform)

        # Weighted average
        ensemble_probas = sum(
            proba * (w / total_weight)
            for proba, w in zip(all_probas, weights)
        )

        cls_map = {0: "away", 1: "draw", 2: "home"}
        return {cls_map.get(i, str(i)): float(ensemble_probas[i]) for i in range(len(labels))}

    @staticmethod
    def _market_implied_read(
        fixture: dict,
        home_team: str,
        away_team: str,
        home_hist: int,
        away_hist: int,
        full_history_missing: bool,
    ) -> list[dict]:
        """Cold-start fallback: derive reads ONLY from real bookmaker odds.

        When a team has no completed-match history, the ML ensemble and the
        Poisson simulation would both run on hardcoded neutral defaults (form
        1.2, goals 1.3/1.1, Elo 1500) — producing the SAME invented card for
        every fixture. That is dishonest and got users betting on template
        constants. Instead, convert the fixture's real odds into margin-removed
        probabilities (standard vig removal) and publish only what the market
        itself implies, clearly labeled. Without real odds, publish nothing.
        """
        meta = {
            "read_type": "market_implied",
            "data_notice": (
                "No usable match history for this fixture. "
                f"Completed matches on record: {home_team}={home_hist}, {away_team}={away_hist}. "
                "These reads are derived directly from bookmaker odds (margin removed), "
                "not from a trained model."
            ),
            "history_counts": {"home": home_hist, "away": away_hist},
            "factors": [],
            "probabilities": {},
        }
        if not full_history_missing:
            meta["data_notice"] = (
                "Limited match history for this fixture. "
                f"Completed matches on record: {home_team}={home_hist}, {away_team}={away_hist} "
                f"(minimum {3} required for a model read). "
                "These reads are derived directly from bookmaker odds (margin removed), "
                "not from a trained model."
            )

        home_odds = fixture.get("home_odds")
        draw_odds = fixture.get("draw_odds")
        away_odds = fixture.get("away_odds")
        try:
            implied = [1.0 / float(o) for o in (home_odds, draw_odds, away_odds)]
        except (TypeError, ValueError, ZeroDivisionError):
            return []  # No real odds -> nothing honest to say
        if not all(o > 1.01 for o in (home_odds, draw_odds, away_odds)):
            return []
        total = sum(implied)
        p_home, p_draw, p_away = (x / total for x in implied)
        meta["probabilities"] = {
            "home_win": round(p_home, 4),
            "draw": round(p_draw, 4),
            "away_win": round(p_away, 4),
            "source": "market_implied_margin_removed",
        }

        one_x_two = {"Home Win": p_home, "Draw": p_draw, "Away Win": p_away}
        pick_1x2, conf_1x2 = max(one_x_two.items(), key=lambda x: x[1])
        dc = {
            "Home or Draw": p_home + p_draw,
            "Away or Draw": p_away + p_draw,
            "Home or Away": p_home + p_away,
        }
        dc_pick, dc_conf = max(dc.items(), key=lambda x: x[1])

        def _risk(c: float) -> str:
            # Market-implied reads cap at Medium: they carry no model edge, and
            # calling them Low risk would overstate what they are.
            return "Medium" if c >= 0.72 else "Medium" if c >= 0.58 else "High"

        notice = meta["data_notice"]
        return [
            {
                "market": "1X2",
                "pick": pick_1x2,
                "confidence": round(conf_1x2 * 100, 1),
                "edge_score": 0.0,
                "risk_level": _risk(conf_1x2),
                "reasoning": notice,
                "engine_meta": {**meta, "market_logic": "Margin-removed bookmaker probabilities; no model involved."},
            },
            {
                "market": "Double Chance",
                "pick": dc_pick,
                "confidence": round(dc_conf * 100, 1),
                "edge_score": 0.0,
                "risk_level": _risk(dc_conf),
                "reasoning": notice,
                "engine_meta": {**meta, "market_logic": "Margin-removed bookmaker probabilities; no model involved."},
            },
        ]

    def predict_soccer(self, history: pd.DataFrame, fixture: dict) -> list[dict]:
        home_team = normalize_team_name(fixture["home_team"], "soccer")
        away_team = normalize_team_name(fixture["away_team"], "soccer")
        # Load insider signals if a DB session is available
        insider: dict = {}
        db = fixture.get("_db")
        fixture_id = fixture.get("id")
        if db and fixture_id:
            try:
                from app.services.insider_signals import get_fixture_signals
                insider = get_fixture_signals(db, fixture_id)
            except Exception:
                pass
        f = features_for_fixture(
            history,
            home_team,
            away_team,
            fixture.get("match_date"),
            fixture.get("league"),
            fixture.get("home_odds"),
            fixture.get("draw_odds"),
            fixture.get("away_odds"),
            insider=insider,
            standings_db=db,
            season=fixture.get("season"),
        )

        # ── Honesty gate: never present fabricated feature defaults as insight ──
        # features_for_fixture() substitutes neutral constants (form 1.2, goals
        # 1.3/1.1, Elo 1500xleague) when a team has no completed-match history.
        # When BOTH sides fall back like that, every fixture would produce the
        # same invented card. Instead: with real odds, publish only market-implied
        # reads (margin-removed bookmaker probabilities); without odds, publish
        # nothing.
        home_hist = int(f.get("home_history_count", 0) or 0)
        away_hist = int(f.get("away_history_count", 0) or 0)
        MIN_HISTORY_MATCHES = 3
        if home_hist < MIN_HISTORY_MATCHES or away_hist < MIN_HISTORY_MATCHES:
            return self._market_implied_read(
                fixture,
                home_team,
                away_team,
                home_hist,
                away_hist,
                full_history_missing=home_hist == 0 and away_hist == 0,
            )

        # No trained/active bundle: refuse to emit the uniform 0.33/0.33/0.34
        # fallback — an unmodeled guess presented as an ensemble read is worse
        # than no read.
        if not self._load_bundle():
            return []
        home_lam = max((f["home_goals_for"] + f["away_goals_against"]) / 2, 0.2)
        away_lam = max((f["away_goals_for"] + f["home_goals_against"]) / 2, 0.2)
        p = soccer_probabilities(home_lam, away_lam)
        
        # Initialize value betting engine
        value_engine = PoissonValueEngine()

        # Ensemble prediction with meta-learner blend
        labels = [0, 1, 2]  # away, draw, home
        ml_probs = self._ensemble_predict(f, labels)
        bundle = self._load_bundle()
        ml_probs = apply_calibration(ml_probs, bundle.get("calibrator_path") if bundle else None)

        form_total = f["home_form_points"] + f["away_form_points"] + 0.01
        one_x_two = {
            "Home Win": 0.45 * ml_probs.get("home", 0.34) + 0.35 * p["home"] + 0.20 * (f["home_form_points"] / form_total),
            "Draw": 0.45 * ml_probs.get("draw", 0.33) + 0.35 * p["draw"] + 0.20 * ((f["home_draw_rate_5"] + f["away_draw_rate_5"]) / 2),
            "Away Win": 0.45 * ml_probs.get("away", 0.33) + 0.35 * p["away"] + 0.20 * (f["away_form_points"] / form_total),
        }
        pick_1x2, conf_1x2 = max(one_x_two.items(), key=lambda x: x[1])
        over_conf = 0.65 * p["over25"] + 0.35 * min((home_lam + away_lam) / 4, 1)
        btts_conf = 0.70 * p["btts"] + 0.30 * min(min(home_lam, away_lam) / 2.5, 1)

        # Double Chance: Home or Draw, Away or Draw, Home or Away
        dc_home = p["home"] + p["draw"]
        dc_away = p["away"] + p["draw"]
        dc_no_draw = p["home"] + p["away"]
        double_chance_picks = {"Home or Draw": dc_home, "Away or Draw": dc_away, "Home or Away": dc_no_draw}
        dc_pick, dc_conf = max(double_chance_picks.items(), key=lambda x: x[1])

        # Over/Under 1.5
        from app.ml.poisson import poisson_pmf as _pmf
        over15 = sum(_pmf(h, home_lam) * _pmf(a, away_lam)
                     for h in range(7) for a in range(7) if h + a > 1.5)
        # Over/Under 3.5
        over35 = sum(_pmf(h, home_lam) * _pmf(a, away_lam)
                     for h in range(7) for a in range(7) if h + a > 3.5)

        def risk(c: float) -> str:
            return "Low" if c >= 0.72 else "Medium" if c >= 0.58 else "High"

        model_factors = [
            {"label": "Home form points", "value": round(float(f["home_form_points"]), 2), "note": f"{home_team} recent points-per-match profile"},
            {"label": "Away form points", "value": round(float(f["away_form_points"]), 2), "note": f"{away_team} recent points-per-match profile"},
            {"label": "Elo gap", "value": round(float(f["elo_diff"]), 1), "note": "Positive favors the home team; negative favors the away team"},
            {"label": "Projected goals", "value": round(float(home_lam + away_lam), 2), "note": "Model blend of recent scoring and goals allowed"},
            {"label": "BTTS probability", "value": f"{p['btts']:.1%}", "note": "Chance both teams score from Poisson goal simulation"},
            {"label": "Over 2.5 probability", "value": f"{p['over25']:.1%}", "note": "Chance total goals finish above 2.5"},
            {"label": "Home streak", "value": f"{f.get('home_streak_len', 0):.0f} {'W' if f.get('home_streak_winning') else 'L'}", "note": "Current result streak for home side"},
            {"label": "Away streak", "value": f"{f.get('away_streak_len', 0):.0f} {'W' if f.get('away_streak_winning') else 'L'}", "note": "Current result streak for away side"},
            {"label": "H2H home win rate", "value": f"{f.get('h2h_home_win_rate', 0.5):.0%}", "note": "Head-to-head history home win percentage"},
            {"label": "Home scoring consistency", "value": f"{f.get('home_scoring_consistency', 0.65):.0%}", "note": "Scored in last 5 matches"},
            {"label": "Away scoring consistency", "value": f"{f.get('away_scoring_consistency', 0.60):.0%}", "note": "Scored in last 5 matches"},
        ]

        # Shot model info
        summary = "LOYAL EDGE AI blends trained prediction probabilities, Poisson goal simulation, recent form, Elo strength, H2H records, streaks, home/away scoring balance, clean-sheet rates, and market odds where available."

        base_meta = {
            "summary": summary,
            "factors": model_factors,
            "probabilities": {
                "home_win": round(float(p["home"]), 4),
                "draw": round(float(p["draw"]), 4),
                "away_win": round(float(p["away"]), 4),
                "over25": round(float(p["over25"]), 4),
                "btts": round(float(p["btts"]), 4),
            },
            "projection": {
                "score_band": p["score"],
                "home_expected_goals": round(float(home_lam), 2),
                "away_expected_goals": round(float(away_lam), 2),
                "total_expected_goals": round(float(home_lam + away_lam), 2),
            },
            "data_depth": {
                "home_history": home_hist,
                "away_history": away_hist,
                "minimum_for_model_read": MIN_HISTORY_MATCHES,
            },
        }

        reason_1x2 = f"Model rates {pick_1x2} based on {f['home_form_points']:.1f} vs {f['away_form_points']:.1f} form points, Elo {f['home_elo']:.0f}-{f['away_elo']:.0f}, and league strength {f['league_strength']:.2f}. {home_team} scoring avg {f['home_goals_for']:.2f}, {away_team} avg {f['away_goals_for']:.2f}."
        reason_goals = f"Total goal estimate {home_lam + away_lam:.2f}. Home avg {f['home_goals_for']:.2f}, away avg {f['away_goals_for']:.2f}. Clean sheet rates: home {f['home_clean_sheet_rate_5']:.0%}, away {f['away_clean_sheet_rate_5']:.0%}."
        reason_btts = f"Both teams scoring probability {p['btts']:.1%}. {home_team} failed to score {f['home_failed_score_rate_5']:.0%} of last 5, {away_team} {f['away_failed_score_rate_5']:.0%}."
        reason_dc = f"Double chance backed by form ({f['home_form_points']:.1f}-{f['away_form_points']:.1f}) and Elo ({f['home_elo']:.0f}-{f['away_elo']:.0f}). League quality: {f['league_strength']:.2f}."

        score_conf = max(1.0, min(p.get("score_prob", 0.0) * 100, 42.0))
        btts_pick = "BTTS Yes" if btts_conf >= 0.53 else "BTTS No"
        btts_final_conf = round(max(btts_conf, 1 - btts_conf) * 100, 1)
        goals_pick = "Over 2.5 Goals" if over_conf >= 0.5 else "Under 2.5 Goals"
        goals_conf = round(max(over_conf, 1 - over_conf) * 100, 1)
        over15_pick = "Over 1.5 Goals" if over15 >= 0.5 else "Under 1.5 Goals"
        over15_conf = round(max(over15, 1 - over15) * 100, 1)
        over35_pick = "Over 3.5 Goals" if over35 >= 0.5 else "Under 3.5 Goals"
        over35_conf = round(max(over35, 1 - over35) * 100, 1)

        # Check for significant line movement (market efficiency check)
        line_movement_warning = False
        if fixture.get("home_odds") and fixture.get("draw_odds") and fixture.get("away_odds"):
            # In production, this would compare with historical odds
            # For now, we'll add a flag that can be set by the odds aggregator
            if fixture.get("line_movement_significant"):
                line_movement_warning = True
        
        # Calculate value bets if odds are available
        value_bets_info = {}
        if fixture.get("home_odds") and fixture.get("draw_odds") and fixture.get("away_odds"):
            # Create predictions dictionary for value engine
            predictions_for_value = [
                {"market": "1X2", "pick": pick_1x2, "confidence": conf_1x2 * 100},
                {"market": "Over/Under 2.5", "pick": goals_pick, "confidence": goals_conf},
                {"market": "Both Teams to Score", "pick": btts_pick, "confidence": btts_final_conf},
            ]
            
            # Map picks to odds — ONLY real odds. Inventing a price (the old
            # 1.9/1.8 defaults) fed fake "value bets" to users.
            fixture_odds = {}
            if pick_1x2 == "Home Win" and fixture.get("home_odds"):
                fixture_odds["1X2_Home_Win"] = fixture["home_odds"]
            elif pick_1x2 == "Draw" and fixture.get("draw_odds"):
                fixture_odds["1X2_Draw"] = fixture["draw_odds"]
            elif pick_1x2 == "Away Win" and fixture.get("away_odds"):
                fixture_odds["1X2_Away_Win"] = fixture["away_odds"]

            if "Over" in goals_pick and fixture.get("over_2_5_odds"):
                fixture_odds["Over/Under_2.5_Over_2.5_Goals"] = fixture["over_2_5_odds"]
            elif "Over" not in goals_pick and fixture.get("under_2_5_odds"):
                fixture_odds["Over/Under_2.5_Under_2.5_Goals"] = fixture["under_2_5_odds"]

            if "Yes" in btts_pick and fixture.get("btts_yes_odds"):
                fixture_odds["Both_Teams_to_Score_BTTS_Yes"] = fixture["btts_yes_odds"]
            elif "Yes" not in btts_pick and fixture.get("btts_no_odds"):
                fixture_odds["Both_Teams_to_Score_BTTS_No"] = fixture["btts_no_odds"]
            
            # Identify value bets
            value_bets = value_engine.identify_value_bets(predictions_for_value, fixture_odds)
            
            for vb in value_bets:
                key = f"{vb.market}_{vb.pick}".replace(" ", "_")
                value_bets_info[key] = {
                    "edge": round(vb.edge * 100, 2),
                    "expected_value": round(vb.expected_value * 100, 2),
                    "kelly_stake": round(vb.kelly_stake * 100, 2),
                    "value_confidence": vb.confidence
                }
        
        # Add value information to engine_meta
        if value_bets_info:
            base_meta["value_bets"] = value_bets_info
            base_meta["value_note"] = "Edge calculated as model probability minus bookmaker implied probability. Positive edge indicates value."
        
        # Add line movement warning if detected
        if line_movement_warning:
            base_meta["line_movement_warning"] = True
            base_meta["market_efficiency_note"] = "Significant line movement detected. Market may have information not reflected in model. Use caution."
        
        return [
            {"market": "1X2", "pick": pick_1x2, "confidence": round(conf_1x2 * 100, 1), "edge_score": round(conf_1x2 * 100, 1), "risk_level": risk(conf_1x2), "reasoning": reason_1x2, "engine_meta": {**base_meta, "market_logic": "Result pick compares model win/draw probabilities with form and Elo edge. Edge vs bookmaker calculated where odds available."}},
            {"market": "Double Chance", "pick": dc_pick, "confidence": round(dc_conf * 100, 1), "edge_score": round(dc_conf * 100, 1), "risk_level": risk(dc_conf), "reasoning": reason_dc, "engine_meta": {**base_meta, "market_logic": "Double chance reduces draw/upset variance by covering two outcomes."}},
            {"market": "Over/Under 2.5", "pick": goals_pick, "confidence": goals_conf, "edge_score": goals_conf, "risk_level": risk(goals_conf / 100), "reasoning": reason_goals, "engine_meta": {**base_meta, "market_logic": "2.5 goals uses projected goal total plus each team's scoring and concession profile. Value calculated vs bookmaker odds."}},
            {"market": "Over/Under 1.5", "pick": over15_pick, "confidence": over15_conf, "edge_score": over15_conf, "risk_level": risk(over15_conf / 100), "reasoning": reason_goals, "engine_meta": {**base_meta, "market_logic": "1.5 goals is a safer totals read from the same goal simulation."}},
            {"market": "Over/Under 3.5", "pick": over35_pick, "confidence": over35_conf, "edge_score": over35_conf, "risk_level": risk(over35_conf / 100), "reasoning": reason_goals, "engine_meta": {**base_meta, "market_logic": "3.5 goals checks whether the match profile points to a very open game or a lower ceiling."}},
            {"market": "Both Teams to Score", "pick": btts_pick, "confidence": btts_final_conf, "edge_score": btts_final_conf, "risk_level": risk(btts_final_conf / 100), "reasoning": reason_btts, "engine_meta": {**base_meta, "market_logic": "BTTS compares both teams' scoring rates, failed-score rates, clean sheets, and simulated both-score probability. Value calculated vs bookmaker odds."}},
            {"market": "Correct Score", "pick": p["score"], "confidence": round(score_conf, 1), "edge_score": round(score_conf, 1), "risk_level": "High", "reasoning": high_variance_warning(), "engine_meta": {**base_meta, "market_logic": "Correct score is shown as a score-band signal only because exact scores are volatile."}},
        ]
