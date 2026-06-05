from math import exp, factorial


def poisson_pmf(k: int, lam: float) -> float:
    lam = max(lam, 0.05)
    return (lam**k * exp(-lam)) / factorial(k)


def soccer_probabilities(home_lambda: float, away_lambda: float, max_goals: int = 6) -> dict:
    home = draw = away = over25 = btts = 0.0
    best = (0, 0, 0.0)
    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            p = poisson_pmf(h, home_lambda) * poisson_pmf(a, away_lambda)
            if h > a:
                home += p
            elif h == a:
                draw += p
            else:
                away += p
            if h + a > 2.5:
                over25 += p
            if h > 0 and a > 0:
                btts += p
            if p > best[2]:
                best = (h, a, p)
    return {"home": home, "draw": draw, "away": away, "over25": over25, "btts": btts, "score": f"{best[0]}-{best[1]}"}
