"""Learning stability gate to clamp extreme pattern weights."""

MAX_PATTERN_WEIGHT = 2.0


def stabilize(pattern: dict) -> dict:
    """Clamp weight fields to prevent overfitting."""
    if not isinstance(pattern, dict):
        return pattern
    weight = pattern.get("weight", 1.0)
    try:
        clamped = min(float(weight), MAX_PATTERN_WEIGHT)
    except (TypeError, ValueError):
        clamped = 1.0
    pattern["weight"] = clamped
    return pattern

