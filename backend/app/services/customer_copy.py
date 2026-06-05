def soccer_reasoning(score_band: str, total_goals: float) -> str:
    """Customer wording is separate from model training.

    Match history trains the model. These templates only control how results are
    explained to customers, so chatbot/speech style never contaminates training.
    """
    return (
        "LOYAL EDGE rating found a positive matchup profile. "
        f"Projected score band: {score_band}; total-goal estimate: {total_goals:.2f}. "
        "Form, home/away balance, scoring trend, and risk filter were checked."
    )


def high_variance_warning() -> str:
    return "Correct scores are high-variance; treat this as a score band, not a guaranteed result."
