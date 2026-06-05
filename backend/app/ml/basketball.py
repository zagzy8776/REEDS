def predict_basketball_fixture(home_recent_points: list[int], away_recent_points: list[int], line_total: float | None = None) -> list[dict]:
    """Lightweight basketball MVP for spread/total leans until full NBA training data is loaded."""

    def avg(values, default):
        return sum(values) / len(values) if values else default

    home_avg = avg(home_recent_points, 112)
    away_avg = avg(away_recent_points, 108)
    projected_total = home_avg + away_avg
    spread_edge = home_avg - away_avg + 2.5
    total_pick = "Over" if line_total and projected_total > line_total else "Under" if line_total else "Projected High Total"
    confidence = min(72, max(52, abs(spread_edge) * 4 + 50))
    return [
        {
            "market": "Spread",
            "pick": "Home Spread Lean" if spread_edge >= 0 else "Away Spread Lean",
            "confidence": round(confidence, 1),
            "reasoning": f"Recent scoring profile projects home {home_avg:.1f}, away {away_avg:.1f}; spread edge {spread_edge:.1f}.",
        },
        {
            "market": "Total Points",
            "pick": total_pick,
            "confidence": 58.0,
            "reasoning": f"Projected combined points: {projected_total:.1f}.",
        },
    ]