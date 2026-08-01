"""Unit tests for deterministic physiological limiter detection (#477).

The engine is pure — it reads an Athlete Performance Model attribute map and
returns a ranked limiter list — so these tests feed synthetic attributes and
assert on the ranking, confidence, evidence and counter-evidence directly.
"""

from services import limiter_detection as ld


def _attr(**kw):
    base = {
        "estimate": None,
        "score": None,
        "confidence": 0.5,
        "unit": None,
        "evidence": [],
        "missing_information": [],
    }
    base.update(kw)
    return base


def _model(*, ftp, map_w, frac, aerobic="high", fatigue="high", vo2=55.0):
    """A model attribute map with the fields limiter detection reads."""
    return {
        "ftp": _attr(estimate=ftp, unit="W", confidence=0.7),
        "map": _attr(estimate=map_w, unit="W", confidence=0.6),
        "vo2max": _attr(estimate=vo2, unit="ml/kg/min", confidence=0.4),
        "fractional_utilization": _attr(estimate=frac, confidence=0.55),
        "aerobic_endurance": _attr(score=aerobic, confidence=0.6),
        "fatigue_resistance": _attr(score=fatigue, confidence=0.5),
    }


def test_canonical_case_selects_threshold_as_top_limiter():
    # High MAP, strong endurance, relatively low FTP -> threshold is the limiter.
    attrs = _model(ftp=250, map_w=380, frac=0.66, aerobic="high", fatigue="high")
    limiters = ld.detect_limiters(attrs)
    top = limiters[0]
    assert top["limiter"] == ld.LIMITER_THRESHOLD
    assert top["confidence"] > 0.0
    assert top["evidence"]  # references the MAP/FTP gap
    assert any("MAP" in e or "aerobic power" in e for e in top["evidence"])
    assert ld.top_limiter(limiters) == ld.LIMITER_THRESHOLD


def test_high_fractional_utilization_selects_vo2max_ceiling():
    # FTP already close to MAP -> the aerobic ceiling itself is limiting.
    attrs = _model(ftp=340, map_w=400, frac=0.85, aerobic="high", fatigue="high")
    limiters = ld.detect_limiters(attrs)
    assert limiters[0]["limiter"] == ld.LIMITER_VO2MAX
    assert limiters[0]["evidence"]


def test_vo2max_unknown_is_counter_evidence_for_ceiling_limiter():
    attrs = _model(ftp=340, map_w=400, frac=0.85, vo2=None)
    attrs["vo2max"] = _attr(score="unknown", confidence=0.05)
    top = ld.detect_limiters(attrs)[0]
    assert top["limiter"] == ld.LIMITER_VO2MAX
    assert any("VO₂max" in c or "weight" in c for c in top["counter_evidence"])


def test_poor_durability_surfaces_endurance_durability():
    attrs = _model(ftp=250, map_w=380, frac=0.66, aerobic="developing", fatigue="fades")
    limiters = ld.detect_limiters(attrs)
    names = {c["limiter"] for c in limiters}
    assert ld.LIMITER_DURABILITY in names
    dur = next(c for c in limiters if c["limiter"] == ld.LIMITER_DURABILITY)
    assert dur["evidence"]


def test_insufficient_data_when_no_signals():
    limiters = ld.detect_limiters({})
    assert len(limiters) == 1
    assert limiters[0]["limiter"] == ld.LIMITER_INSUFFICIENT
    assert limiters[0]["confidence"] <= 0.1
    assert ld.top_limiter(limiters) is None


def test_missing_map_does_not_guess_a_power_limiter():
    attrs = {
        "ftp": _attr(estimate=250, unit="W", confidence=0.5),
        "map": _attr(score="unknown", confidence=0.05),
        "fractional_utilization": _attr(score="unknown", confidence=0.05),
        "aerobic_endurance": _attr(score="unknown", confidence=0.1),
        "fatigue_resistance": _attr(score="unknown", confidence=0.1),
    }
    limiters = ld.detect_limiters(attrs)
    assert limiters[0]["limiter"] == ld.LIMITER_INSUFFICIENT


def test_ranking_is_confidence_descending():
    attrs = _model(ftp=250, map_w=380, frac=0.66, aerobic="developing", fatigue="fades")
    limiters = ld.detect_limiters(attrs)
    confs = [c["confidence"] for c in limiters]
    assert confs == sorted(confs, reverse=True)


def test_low_confidence_top_limiter_is_not_promoted():
    # A weak signal (frac just under the band) with low input confidence should
    # stay off likely_limiter rather than being asserted.
    attrs = _model(ftp=250, map_w=355, frac=0.71, aerobic="unknown", fatigue="unknown")
    for key in ("ftp", "map", "fractional_utilization"):
        attrs[key]["confidence"] = 0.15
    attrs["aerobic_endurance"] = _attr(score="unknown", confidence=0.1)
    attrs["fatigue_resistance"] = _attr(score="unknown", confidence=0.1)
    limiters = ld.detect_limiters(attrs)
    assert limiters[0]["confidence"] < ld.LIKELY_LIMITER_MIN_CONFIDENCE
    assert ld.top_limiter(limiters) is None
