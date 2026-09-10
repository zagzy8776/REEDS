"""JSON-safe prediction signatures (avoid tuple/list JSON round-trip spam)."""
from __future__ import annotations


def prediction_signature(item: dict) -> list:
    """Return a JSON-safe signature (list, not tuple).

    Must survive SQLAlchemy JSON round-trip: tuples become lists on read,
    so storing a tuple caused every refresh to look like a change and spam
    new prediction versions.
    """
    meta = item.get("engine_meta") if isinstance(item.get("engine_meta"), dict) else {}
    probs = meta.get("probabilities") if isinstance(meta.get("probabilities"), dict) else {}
    normalized_probs = [[str(k), round(float(v), 4)] for k, v in sorted(probs.items()) if isinstance(v, (int, float))]
    feedback = meta.get("learning_feedback") if isinstance(meta.get("learning_feedback"), dict) else {}
    live_context = meta.get("live_context") if isinstance(meta.get("live_context"), dict) else {}
    return [
        str(item.get("market", "")),
        str(item.get("pick", "")),
        round(float(item.get("confidence", 0)), 3),
        round(float(item.get("edge_score", 0)), 5),
        normalized_probs,
        round(float(feedback.get("adjustment", 0)), 3),
        round(float(live_context.get("adjustment", 0)), 3),
        str(live_context.get("score", "")),
    ]


def normalize_signature(value) -> list:
    """Coerce a stored signature (list or legacy tuple) into a comparable list."""
    if value is None:
        return []
    if isinstance(value, tuple):
        value = list(value)
    if not isinstance(value, list):
        return []
    out = []
    for part in value:
        if isinstance(part, tuple):
            out.append(list(part))
        elif isinstance(part, list):
            out.append([list(x) if isinstance(x, tuple) else x for x in part])
        else:
            out.append(part)
    return out


def existing_prediction_changed(pred, item: dict) -> bool:
    meta = pred.engine_meta if isinstance(pred.engine_meta, dict) else {}
    stored = normalize_signature(meta.get("prediction_signature"))
    current = normalize_signature(prediction_signature(item))
    return stored != current
