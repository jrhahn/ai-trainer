"""Unit tests for the deterministic ROI recommendation engine (#478).

The engine is pure: it maps the Athlete Performance Model attributes + the ranked
limiter list (from :mod:`services.limiter_detection`) to an expected-gain-per-system
map, a weekly emphasis and a natural-language rationale. No DB, no LLM.
"""

from services import limiter_detection
from services.roi_recommendation import (
    GAIN_LARGE,
    SYSTEM_ENDURANCE,
    SYSTEM_THRESHOLD,
    SYSTEM_VO2MAX,
    recommend_training_roi,
)


def _attr(estimate=None, score=None, confidence=0.6):
    return {"estimate": estimate, "score": score, "confidence": confidence}


def _threshold_limited():
    """FTP well below MAP -> threshold is the limiter."""
    return {
        "ftp": _attr(estimate=250),
        "map": _attr(estimate=360),
        "fractional_utilization": _attr(estimate=0.69),
        "vo2max": _attr(estimate=62),
        "aerobic_endurance": _attr(score="high"),
        "fatigue_resistance": _attr(score="above_average"),
    }


def _vo2max_limited():
    """FTP close to MAP -> the ceiling itself limits."""
    return {
        "ftp": _attr(estimate=320),
        "map": _attr(estimate=375),
        "fractional_utilization": _attr(estimate=0.85),
        "vo2max": _attr(estimate=64),
        "aerobic_endurance": _attr(score="above_average"),
        "fatigue_resistance": _attr(score="high"),
    }


def _durability_limited():
    """Mid fractional utilization but power fades late -> durability."""
    return {
        "ftp": _attr(estimate=280),
        "map": _attr(estimate=375),
        "fractional_utilization": _attr(estimate=0.75),
        "aerobic_endurance": _attr(score="developing"),
        "fatigue_resistance": _attr(score="fades"),
    }


def _roi(attrs):
    limiters = limiter_detection.detect_limiters(attrs)
    return recommend_training_roi(attrs, limiters)


def _gain_for(rec, system):
    for g in rec["expected_gain"]:
        if g["system"] == system:
            return g["gain"]
    raise AssertionError(f"{system} missing from expected_gain")


def _sessions_for(rec, system):
    for e in rec["weekly_emphasis"]:
        if e["system"] == system:
            return e["sessions"]
    return 0


def test_threshold_limiter_makes_threshold_the_highest_return():
    rec = _roi(_threshold_limited())
    assert rec["sufficient"] is True
    assert rec["limiter"] == "threshold"
    # Threshold ranks first and carries the largest expected gain.
    assert rec["expected_gain"][0]["system"] == SYSTEM_THRESHOLD
    assert _gain_for(rec, SYSTEM_THRESHOLD) == GAIN_LARGE
    # VO2max is already relatively developed -> less than threshold.
    assert _gain_for(rec, SYSTEM_VO2MAX) != GAIN_LARGE
    # Weekly emphasis doubles the limiter and keeps a long ride.
    assert _sessions_for(rec, SYSTEM_THRESHOLD) == 2
    assert _sessions_for(rec, SYSTEM_ENDURANCE) >= 1


def test_rationale_cites_the_athletes_own_numbers():
    rec = _roi(_threshold_limited())
    # FTP and MAP estimates appear in the natural-language rationale.
    assert "250" in rec["rationale"] and "360" in rec["rationale"]
    assert rec["hypothesis"]
    assert 0.0 < rec["confidence"] <= 1.0


def test_vo2max_limiter_prioritises_the_ceiling():
    rec = _roi(_vo2max_limited())
    assert rec["sufficient"] is True
    assert rec["limiter"] == "vo2max"
    assert rec["expected_gain"][0]["system"] == SYSTEM_VO2MAX
    assert _gain_for(rec, SYSTEM_VO2MAX) == GAIN_LARGE
    assert _sessions_for(rec, SYSTEM_VO2MAX) == 2


def test_durability_limiter_prioritises_endurance():
    rec = _roi(_durability_limited())
    assert rec["sufficient"] is True
    assert rec["limiter"] == "endurance_durability"
    assert _gain_for(rec, SYSTEM_ENDURANCE) == GAIN_LARGE
    assert _sessions_for(rec, SYSTEM_ENDURANCE) == 2


def test_insufficient_model_falls_back_gracefully():
    rec = recommend_training_roi({}, limiter_detection.detect_limiters({}))
    assert rec["sufficient"] is False
    assert rec["limiter"] is None
    assert rec["confidence"] == 0.0
    # Still returns a balanced, machine-readable structure the caller can ignore.
    assert rec["expected_gain"] and rec["weekly_emphasis"]
    assert rec["rationale"]


def test_none_limiters_is_treated_as_insufficient():
    rec = recommend_training_roi(_threshold_limited(), None)
    assert rec["sufficient"] is False
    assert rec["weekly_emphasis"]


def test_expected_gain_is_sorted_by_return():
    rec = _roi(_threshold_limited())
    ranks = [g["gain"] for g in rec["expected_gain"]]
    order = {"large": 3, "moderate": 2, "small": 1, "maintenance": 0}
    assert ranks == sorted(ranks, key=lambda g: order[g], reverse=True)
