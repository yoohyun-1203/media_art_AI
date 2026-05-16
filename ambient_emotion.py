import math


NEUTRAL_MOOD = {"valence": 0.0, "arousal": 0.0, "confidence": 0.0}


def _finite_float(value, default=0.0):
    number = float(value)
    if not math.isfinite(number):
        return default
    return number


def _clamp(value, low, high):
    return max(low, min(high, value))


def average_mood(items):
    total_weight = 0.0
    weighted_valence = 0.0
    weighted_arousal = 0.0
    contributing_items = 0

    for item in items:
        confidence = _clamp(_finite_float(item.get("confidence", 0.0)), 0.0, 1.0)
        if confidence == 0.0:
            continue

        total_weight += confidence
        weighted_valence += _finite_float(item.get("valence", 0.0)) * confidence
        weighted_arousal += _finite_float(item.get("arousal", 0.0)) * confidence
        contributing_items += 1

    if total_weight == 0.0:
        return dict(NEUTRAL_MOOD)

    return {
        "valence": _clamp(weighted_valence / total_weight, -1.0, 1.0),
        "arousal": _clamp(weighted_arousal / total_weight, -1.0, 1.0),
        "confidence": min(1.0, total_weight / contributing_items),
    }
