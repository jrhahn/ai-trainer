"""The limiter / ROI / hypothesis chain, parametrised per sport (#718).

The #474 chain reasons in watts: MAP against FTP, fractional utilisation, a power
curve. Run over a multisport athlete unchanged it would assert a runner's limiter
out of cycling evidence — and it already did something nearly as bad, telling a
run-only athlete it needed "FTP, MAP and long-ride data" before it could help.

These tests pin the four acceptance criteria directly: no limiter for a sport
with no data, same-sport evidence only, an unchanged reading for a cycling-only
history, and at most one top limiter per sport the athlete actually trains.
"""

from __future__ import annotations

import importlib.util
import re
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
from services import athlete_model_inference as ami
from services import hypothesis_engine as he
from services import limiter_detection as ld
from services import roi_recommendation as roi
from services.activity_identity import SPORT_CYCLING, SPORT_RUNNING
from tests.conftest import TestSessionLocal

# "a wattage appears in this text". Not ``" W" in text``, which matches the
# space before any sentence starting with "With" — the first version of these
# assertions passed for two of three running limiters and failed the third on a
# word, not on a watt.
_WATTS = re.compile(r"\d\s*W\b")

BACKEND_DIR = Path(__file__).parent.parent
MIGRATION_PATH = (
    BACKEND_DIR / "alembic" / "versions" / "20261006_000001_add_hypothesis_sport.py"
)


@pytest_asyncio.fixture
async def db() -> AsyncSession:
    """A fresh session, rolled back after each test (the house pattern)."""
    async with TestSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


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


def _cycling(*, ftp=250, map_w=380, frac=0.66, aerobic="high", fatigue="high"):
    """The canonical cycling attribute map the #477 tests use."""
    return {
        "ftp": _attr(estimate=ftp, unit="W", confidence=0.7),
        "map": _attr(estimate=map_w, unit="W", confidence=0.6),
        "vo2max": _attr(estimate=55.0, unit="ml/kg/min", confidence=0.4),
        "fractional_utilization": _attr(estimate=frac, confidence=0.55),
        "aerobic_endurance": _attr(score=aerobic, confidence=0.6),
        "fatigue_resistance": _attr(score=fatigue, confidence=0.5),
    }


def _running(*, cs=3.6, ceiling=4.3, fatigue="high"):
    """A running attribute map. ``cs``/``ceiling`` in m/s, as the model stores them."""
    frac = cs / ceiling if ceiling else None
    return {
        "critical_speed": _attr(estimate=cs, unit="m/s", confidence=0.6),
        "d_prime": _attr(estimate=180.0, unit="m", confidence=0.6),
        "threshold_pace": _attr(estimate=289.0, unit="s/km", confidence=0.6),
        "velocity_at_vo2max": _attr(estimate=ceiling, unit="m/s", confidence=0.55),
        "run_fractional_utilization": _attr(estimate=frac, confidence=0.45),
        "run_fatigue_resistance": _attr(score=fatigue, confidence=0.5),
    }


def _unknown_running():
    """Running attributes that exist but say nothing — a cyclist's model."""
    return {
        key: _attr(score="unknown", confidence=0.1) for key in ld.RUNNING_ATTRIBUTES
    }


# ---------------------------------------------------------------------------
# The attributes the running rules need (#718 additions to the inference engine)
# ---------------------------------------------------------------------------


def _run_signal(**kw) -> tuple[SimpleNamespace, dict]:
    sig = {"sport": "running", "distance_m": 10000, "duration_s": 3600}
    sig.update(kw)
    return SimpleNamespace(activity_date="2026-10-01", ftp_used=None), sig


def test_the_running_aerobic_ceiling_comes_from_the_five_minute_point():
    envelope = {1.0: (5.4, 1, "2026-10-01"), 5.0: (4.5, 2, "2026-10-01")}
    attr = ami._infer_velocity_at_vo2max(envelope, 7)
    assert attr["estimate"] == pytest.approx(4.5)
    assert attr["unit"] == "m/s"
    # Stated as a pace, because 4.5 m/s is not a number a runner recognises.
    assert "/km" in attr["evidence"][0]


def test_without_a_five_minute_effort_the_ceiling_is_unknown_with_a_protocol():
    attr = ami._infer_velocity_at_vo2max({1.0: (5.4, 1, "2026-10-01")}, 7)
    assert attr["estimate"] is None
    assert attr["score"] == "unknown"
    assert "5-minute run" in attr["validation_protocol"]


def test_the_ceiling_says_it_reads_slightly_fast():
    """5 min is shorter than the ~6 min vVO₂max is defined at.

    The limitation is on the attribute rather than in a comment, because the
    direction of the error decides which limiter fires.
    """
    attr = ami._infer_velocity_at_vo2max({5.0: (4.5, 2, None)}, 7)
    assert any("reads slightly fast" in m for m in attr["missing_information"])


def test_run_fractional_utilization_is_critical_speed_over_the_ceiling():
    attr = ami._infer_run_fractional_utilization(3.6, 4.5)
    assert attr["estimate"] == pytest.approx(0.8)


@pytest.mark.parametrize(
    "cs,ceiling", [(None, 4.5), (3.6, None), (3.6, 0.0)], ids=["no CS", "no ceiling", "zero ceiling"]
)
def test_run_fractional_utilization_refuses_without_both_halves(cs, ceiling):
    attr = ami._infer_run_fractional_utilization(cs, ceiling)
    assert attr["estimate"] is None
    assert attr["score"] == "unknown"


def test_run_durability_reads_the_half_split_pace():
    signals = [
        _run_signal(duration_s=4200, first_half_speed=3.5, second_half_speed=3.2),
        _run_signal(duration_s=4000, first_half_speed=3.5, second_half_speed=3.25),
    ]
    attr = ami._infer_run_fatigue_resistance(signals)
    assert attr["score"] == "fades"
    assert "long run(s)" in attr["evidence"][0]


def test_the_running_durability_bands_are_tighter_than_the_cycling_ones():
    """A 95 % hold is "moderate" on foot and would be "above_average" on a bike.

    A runner's speed varies far less than a cyclist's power — no coasting, and
    the hills are already out via grade adjustment — so reused cycling bands
    would score every run "high" and the attribute would never say anything.
    """
    signals = [_run_signal(duration_s=4200, first_half_speed=4.0, second_half_speed=3.8)]
    run = ami._infer_run_fatigue_resistance(signals)
    rides = [
        (
            SimpleNamespace(activity_date="2026-10-01"),
            {"duration_s": 6000, "first_half_power": 200, "second_half_power": 190},
        )
    ]
    ride = ami._infer_fatigue_resistance(rides)
    assert run["score"] == "moderate"
    assert ride["score"] == "above_average"


@pytest.mark.parametrize(
    "second,expected",
    [
        (4.0, "high"),
        (3.92, "above_average"),
        (3.8, "moderate"),
        (3.6, "fades"),
    ],
)
def test_every_run_durability_band_is_reachable(second: float, expected: str):
    """A band nothing can land in is a band that does not exist."""
    signals = [_run_signal(duration_s=4200, first_half_speed=4.0, second_half_speed=second)]
    assert ami._infer_run_fatigue_resistance(signals)["score"] == expected


def test_a_short_run_is_not_durability_evidence():
    signals = [_run_signal(duration_s=1800, first_half_speed=3.5, second_half_speed=3.0)]
    attr = ami._infer_run_fatigue_resistance(signals)
    assert attr["score"] == "unknown"
    assert attr["missing_information"]


def test_the_ceiling_and_durability_survive_a_refused_critical_speed_fit():
    """A runner whose pace collapses has a limiter whether or not CS can be fitted.

    Withholding the whole running model because one attribute of it is unknown is
    how a sport ends up invisible to the chain.
    """
    signals = [
        _run_signal(
            duration_s=4200,
            first_half_speed=3.5,
            second_half_speed=3.1,
            # One point only: nowhere near enough for a Critical Speed fit.
            gap_speed_curve={"5": 4.4},
        )
    ]
    attrs = ami._infer_run_attributes(signals, 7)
    assert attrs["critical_speed"]["score"] == "unknown"
    assert attrs["velocity_at_vo2max"]["estimate"] == pytest.approx(4.4)
    assert attrs["run_fatigue_resistance"]["score"] == "fades"


# ---------------------------------------------------------------------------
# A cycling-only history must read exactly as it did before
# ---------------------------------------------------------------------------


# The pre-#718 ranking for four differently-shaped cycling models, as
# ``(limiter id, confidence)`` pairs. **Captured by executing the
# ``services/limiter_detection.py`` from ``origin/develop`` over the same
# attribute maps**, not written from reading the code — a baseline asserted from
# memory tells you nothing about what the old code did, and the first number I
# wrote by hand here was wrong by 0.22.
_PRE_718_CYCLING_RANKING = {
    "threshold": [("threshold", 0.53)],
    "vo2max": [("vo2max", 0.4)],
    "durability": [("endurance_durability", 0.59), ("threshold", 0.43)],
    "nothing_conclusive": [("insufficient_data", 0.05)],
}

_CYCLING_SHAPES = {
    "threshold": dict(ftp=250, map_w=380, frac=0.66),
    "vo2max": dict(ftp=320, map_w=380, frac=0.84),
    "durability": dict(
        ftp=250, map_w=380, frac=0.66, aerobic="developing", fatigue="fades"
    ),
    "nothing_conclusive": dict(ftp=250, map_w=380, frac=0.76),
}


@pytest.mark.parametrize("shape", sorted(_CYCLING_SHAPES))
def test_a_cycling_only_history_reads_exactly_as_before(shape: str):
    """The third acceptance criterion, stated precisely and over four shapes.

    Every limiter the cycling rules can produce, plus the "nothing conclusive"
    case, against the ranking the pre-change module actually returned. The only
    permitted difference is the additive ``sport`` field.
    """
    attrs = _cycling(**_CYCLING_SHAPES[shape])
    limiters = ld.detect_limiters(attrs)
    assert [
        (c["limiter"], c["confidence"]) for c in limiters
    ] == _PRE_718_CYCLING_RANKING[shape]
    assert all(c["sport"] == SPORT_CYCLING for c in limiters)
    assert all(set(c) == {"limiter", "sport", "confidence", "evidence", "counter_evidence"} for c in limiters)


def test_equal_confidences_keep_rule_order_rather_than_alphabetical_order():
    """The tie-break is the one place the new sort could reorder a cyclist.

    The pre-#718 sort was stable on a confidence-only key, so a tie kept the
    order the rules produced — the primary candidate before the durability one.
    A ``(-confidence, sport, limiter)`` key would put "endurance_durability"
    first instead, and ties are not rare because confidences are rounded to 2 dp
    before they reach the sort.

    The candidate list is substituted through the rules table rather than coaxed
    out of an attribute map, because a genuine tie is hard to construct and the
    sort key is the thing under test. No test-only parameter on the production
    function — the table is the seam #718 built.
    """
    tied = [
        ld._limiter(ld.LIMITER_THRESHOLD, 0.5, ["primary"], []),
        ld._limiter(ld.LIMITER_DURABILITY, 0.5, ["secondary"], []),
    ]
    rules = ((SPORT_CYCLING, ld.CYCLING_ATTRIBUTES, lambda _attrs: tied, "n/a"),)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(ld, "_SPORT_RULES", rules)
        ranked = ld.detect_limiters(_cycling())
    assert [c["limiter"] for c in ranked] == [
        ld.LIMITER_THRESHOLD,
        ld.LIMITER_DURABILITY,
    ]
    assert ld.LIMITER_DURABILITY < ld.LIMITER_THRESHOLD, (
        "an alphabetical tie-break would have reversed them"
    )


def test_a_model_where_nothing_is_known_names_no_sport():
    """A documented difference from pre-#718, not an accident.

    A cycling-only model whose attributes all read ``unknown`` used to get the
    "need FTP, MAP and long-ride data" entry. It now gets the sportless one,
    because that branch fires when *no* sport has signal — and the old wording
    told a run-only athlete in exactly the same position to go and measure their
    FTP. The cycling wording is still used whenever cycling actually has data
    (see the ``nothing_conclusive`` baseline above).
    """
    attrs = {key: _attr(score="unknown", confidence=0.1) for key in ld.CYCLING_ATTRIBUTES}
    top = ld.detect_limiters(attrs)[0]
    assert top["limiter"] == ld.LIMITER_INSUFFICIENT
    assert top["sport"] is None
    assert "FTP" not in " ".join(top["counter_evidence"])


def test_the_top_limiter_for_a_sport_does_not_trust_the_order_it_was_handed():
    """These lists are also read back out of a stored ``limiters`` column.

    ``top_limiter`` reads ``limiters[0]`` because a freshly detected ranking is
    sorted by contract; a persisted one re-serialised by something that did not
    preserve order would otherwise yield a silently wrong "top" limiter, which
    reads as a coaching opinion.
    """
    shuffled = [
        {"limiter": ld.LIMITER_DURABILITY, "sport": SPORT_CYCLING, "confidence": 0.40},
        {"limiter": ld.LIMITER_THRESHOLD, "sport": SPORT_CYCLING, "confidence": 0.70},
    ]
    assert ld.top_limiter_for_sport(shuffled, SPORT_CYCLING) == ld.LIMITER_THRESHOLD
    assert ld.top_limiter(shuffled) == ld.LIMITER_DURABILITY, (
        "top_limiter keeps reading the ranking as given — that is its contract"
    )


def test_the_cycling_evidence_text_is_unchanged():
    """Separate from the ranking, because prose is what the athlete reads."""
    attrs = _cycling(ftp=250, map_w=380, frac=0.66)
    top = ld.detect_limiters(attrs)[0]
    assert top["evidence"] == [
        "Threshold is only 66% of maximal aerobic power (FTP 250 W vs MAP 380 W); "
        "a well-developed threshold sits near 72-77% of MAP, so there is headroom "
        "to raise FTP toward the aerobic ceiling.",
        "Aerobic endurance looks high, indicating a strong base to build threshold "
        "on.",
    ]
    assert top["counter_evidence"] == []
    assert ld.top_limiter(ld.detect_limiters(attrs)) == ld.LIMITER_THRESHOLD


def test_a_cyclist_with_no_running_data_gets_no_running_limiter():
    attrs = {**_cycling(), **_unknown_running()}
    limiters = ld.detect_limiters(attrs)
    assert all(c["sport"] == SPORT_CYCLING for c in limiters)
    assert ld.top_limiter_by_sport(limiters) == {SPORT_CYCLING: ld.LIMITER_THRESHOLD}


def test_an_attribute_that_says_unknown_is_not_data():
    """The inference engine reporting that it could not tell is not a signal.

    Reading presence as data is how a sport the athlete has never done acquires
    a limiter and an "insufficient running data" paragraph.
    """
    assert ld._has_signal(_unknown_running(), ld.RUNNING_ATTRIBUTES) is False
    assert ld._has_signal(_running(), ld.RUNNING_ATTRIBUTES) is True
    assert ld._has_signal({}, ld.RUNNING_ATTRIBUTES) is False


def test_an_athlete_with_nothing_is_told_so_without_being_asked_for_watts():
    """The old wording told a run-only athlete to go and measure their FTP."""
    limiters = ld.detect_limiters({})
    assert len(limiters) == 1
    assert limiters[0]["limiter"] == ld.LIMITER_INSUFFICIENT
    assert limiters[0]["sport"] is None
    joined = " ".join(limiters[0]["counter_evidence"])
    assert "FTP" not in joined and "MAP" not in joined
    assert ld.top_limiter(limiters) is None


# ---------------------------------------------------------------------------
# The running limiter rules
# ---------------------------------------------------------------------------


def test_a_runner_whose_critical_speed_lags_the_ceiling_is_pace_limited():
    attrs = _running(cs=3.5, ceiling=4.5)  # 78 % — well under the band
    limiters = ld.detect_limiters(attrs)
    top = limiters[0]
    assert top["limiter"] == ld.LIMITER_RUN_CRITICAL_SPEED
    assert top["sport"] == SPORT_RUNNING
    assert top["evidence"]
    assert top["counter_evidence"], "the ceiling is an unverified training effort"


def test_a_runner_whose_critical_speed_is_near_the_ceiling_is_ceiling_limited():
    attrs = _running(cs=4.3, ceiling=4.5)  # 96 %
    limiters = ld.detect_limiters(attrs)
    assert limiters[0]["limiter"] == ld.LIMITER_RUN_SPEED_CEILING
    assert limiters[0]["sport"] == SPORT_RUNNING


def test_a_runner_in_the_middle_of_the_band_gets_no_speed_limiter():
    attrs = _running(cs=4.0, ceiling=4.5)  # 89 %, right where CS should sit
    limiters = ld.detect_limiters(attrs)
    assert ld.LIMITER_RUN_CRITICAL_SPEED not in {c["limiter"] for c in limiters}
    assert ld.LIMITER_RUN_SPEED_CEILING not in {c["limiter"] for c in limiters}


def test_the_cycling_bands_are_not_used_for_running():
    """A ratio of 0.82 is ceiling-limited on a bike and pace-limited on foot.

    CS sits near 90 % of vVO₂max where FTP sits near 75 % of MAP, so reusing the
    cycling numbers would call every runner alive ceiling-limited and send them
    all to the track — the highest-injury-risk sessions in the sport.
    """
    ratio = 0.82
    assert ratio >= ld.FRAC_VO2_CEILING, "on the cycling scale this is ceiling-limited"
    assert ratio < ld.RUN_FRAC_CS_LIMITED, "on the running scale it is pace-limited"

    running = ld.detect_limiters(_running(cs=ratio * 4.5, ceiling=4.5))
    assert running[0]["limiter"] == ld.LIMITER_RUN_CRITICAL_SPEED
    cycling = ld.detect_limiters(_cycling(ftp=int(ratio * 380), map_w=380, frac=ratio))
    assert cycling[0]["limiter"] == ld.LIMITER_VO2MAX


def test_running_durability_fires_only_when_pace_actually_fades():
    fades = ld.detect_limiters(_running(fatigue="fades"))
    holds = ld.detect_limiters(_running(fatigue="high"))
    assert ld.LIMITER_RUN_DURABILITY in {c["limiter"] for c in fades}
    assert ld.LIMITER_RUN_DURABILITY not in {c["limiter"] for c in holds}


def test_running_durability_does_not_borrow_the_cycling_aerobic_score():
    """There is no running equivalent of ``aerobic_endurance``.

    It is built from cardiac drift on long *rides*. The cycling durability rule
    also fires on a weak one; the running rule rests on half-split pace alone
    rather than standing a cycling judgement in for the half it does not have.
    """
    attrs = {
        **_running(fatigue="high"),
        "aerobic_endurance": _attr(score="developing", confidence=0.6),
    }
    limiters = ld.detect_limiters(attrs)
    run_limiters = {c["limiter"] for c in ld.limiters_for_sport(limiters, SPORT_RUNNING)}
    assert ld.LIMITER_RUN_DURABILITY not in run_limiters


# ---------------------------------------------------------------------------
# Same-sport evidence, and one top limiter per sport
# ---------------------------------------------------------------------------


def test_a_running_limiter_never_cites_watts():
    attrs = {**_cycling(ftp=250, map_w=380, frac=0.66), **_running(cs=3.5, ceiling=4.5)}
    for candidate in ld.limiters_for_sport(ld.detect_limiters(attrs), SPORT_RUNNING):
        text = " ".join(candidate["evidence"] + candidate["counter_evidence"])
        assert not _WATTS.search(text)
        assert "FTP" not in text and "MAP" not in text


def test_a_cycling_limiter_never_cites_a_pace():
    attrs = {**_cycling(ftp=250, map_w=380, frac=0.66), **_running(cs=3.5, ceiling=4.5)}
    for candidate in ld.limiters_for_sport(ld.detect_limiters(attrs), SPORT_CYCLING):
        text = " ".join(candidate["evidence"] + candidate["counter_evidence"])
        assert "/km" not in text
        assert "Critical Speed" not in text


def test_a_multisport_athlete_gets_one_top_limiter_per_sport():
    """The fourth acceptance criterion, with both sports genuinely limited."""
    attrs = {
        **_cycling(ftp=250, map_w=380, frac=0.66, aerobic="developing", fatigue="fades"),
        **_running(cs=3.5, ceiling=4.5, fatigue="fades"),
    }
    limiters = ld.detect_limiters(attrs)
    by_sport = ld.top_limiter_by_sport(limiters)
    assert set(by_sport) == {SPORT_CYCLING, SPORT_RUNNING}
    assert by_sport[SPORT_CYCLING] in (ld.LIMITER_THRESHOLD, ld.LIMITER_DURABILITY)
    assert by_sport[SPORT_RUNNING] in (
        ld.LIMITER_RUN_CRITICAL_SPEED,
        ld.LIMITER_RUN_DURABILITY,
    )
    for candidate in limiters:
        assert candidate["confidence"] > 0
        assert candidate["evidence"] or candidate["counter_evidence"]


def test_the_ranking_is_confidence_descending_with_a_stable_tie_break():
    """A ranking that depended on dict order would not be reproducible.

    It is persisted and re-read, so two runs over the same attributes have to
    agree on it.
    """
    attrs = {
        **_cycling(ftp=250, map_w=380, frac=0.66, aerobic="developing", fatigue="fades"),
        **_running(cs=3.5, ceiling=4.5, fatigue="fades"),
    }
    first = ld.detect_limiters(attrs)
    second = ld.detect_limiters(dict(reversed(list(attrs.items()))))
    assert [c["limiter"] for c in first] == [c["limiter"] for c in second]
    confidences = [c["confidence"] for c in first]
    assert confidences == sorted(confidences, reverse=True)


def test_a_sport_with_data_but_nothing_conclusive_is_told_what_it_is_missing():
    attrs = {
        **_cycling(ftp=250, map_w=380, frac=0.76),  # in the band, no candidate
        "critical_speed": _attr(estimate=4.0, unit="m/s", confidence=0.6),
        "velocity_at_vo2max": _attr(score="unknown", confidence=0.1),
        "run_fractional_utilization": _attr(score="unknown", confidence=0.1),
        "run_fatigue_resistance": _attr(score="unknown", confidence=0.1),
    }
    limiters = ld.detect_limiters(attrs)
    by_sport = {c["sport"]: c for c in limiters}
    assert by_sport[SPORT_RUNNING]["limiter"] == ld.LIMITER_INSUFFICIENT
    assert "Critical Speed fit" in by_sport[SPORT_RUNNING]["counter_evidence"][0]
    assert "FTP, MAP" in by_sport[SPORT_CYCLING]["counter_evidence"][0]


def test_an_insufficient_entry_never_outranks_a_real_finding():
    """It is the absence of a finding; a top limiter landing on it reports a gap."""
    attrs = {
        **_running(cs=3.5, ceiling=4.5),
        "ftp": _attr(estimate=250, unit="W", confidence=0.7),
        "map": _attr(score="unknown", confidence=0.05),
        "fractional_utilization": _attr(score="unknown", confidence=0.05),
        "aerobic_endurance": _attr(score="unknown", confidence=0.1),
        "fatigue_resistance": _attr(score="unknown", confidence=0.1),
    }
    limiters = ld.detect_limiters(attrs)
    assert limiters[-1]["limiter"] == ld.LIMITER_INSUFFICIENT
    assert limiters[0]["limiter"] == ld.LIMITER_RUN_CRITICAL_SPEED
    assert ld.top_limiter_for_sport(limiters, SPORT_CYCLING) is None


@pytest.mark.parametrize(
    "speed", [None, 0.0, -1.0, "fast", True], ids=["none", "zero", "negative", "text", "bool"]
)
def test_an_unusable_speed_becomes_a_label_rather_than_a_crash(speed):
    """One answer for a malformed speed, shared by all three stages.

    The three of them used to carry a copy each, which is three places for
    "unknown pace" to drift into a traceback inside a coach prompt.
    """
    assert ld.pace_label(speed) == "unknown pace"
    assert ld.pace_label(3.6).endswith("/km")


def test_a_pre_718_row_with_no_sport_reads_as_cycling():
    """Every limiter stored before this change was about cycling."""
    stored = [
        {"limiter": "threshold", "confidence": 0.7, "evidence": [], "counter_evidence": []}
    ]
    assert ld.limiters_for_sport(stored, SPORT_CYCLING) == stored
    assert ld.limiters_for_sport(stored, SPORT_RUNNING) == []
    assert ld.top_limiter_for_sport(stored, SPORT_CYCLING) == "threshold"


# ---------------------------------------------------------------------------
# ROI: never a cycling intervention on running evidence
# ---------------------------------------------------------------------------


_RUN_LIMITED = _running(cs=3.5, ceiling=4.5)


def test_a_running_limiter_yields_only_running_systems():
    limiters = ld.detect_limiters(_RUN_LIMITED)
    rec = roi.recommend_training_roi(_RUN_LIMITED, limiters)
    assert rec["sufficient"] is True
    assert rec["sport"] == SPORT_RUNNING
    systems = {g["system"] for g in rec["expected_gain"]}
    assert systems <= {
        roi.SYSTEM_RUN_THRESHOLD,
        roi.SYSTEM_RUN_VO2MAX,
        roi.SYSTEM_RUN_ENDURANCE,
    }
    assert all(
        e["system"].startswith("run_") for e in rec["weekly_emphasis"]
    )


def test_the_utility_board_never_offers_a_bike_for_a_running_limiter():
    """The concrete form of "no cycling intervention on running evidence".

    Before #718 a runner whose limiter was durability was offered a gravel ride
    as the way to fix it — ranked, scored, with nothing saying the evidence
    behind the ranking was about their legs on foot.
    """
    limiters = ld.detect_limiters(_RUN_LIMITED)
    rec = roi.recommend_training_roi(
        _RUN_LIMITED, limiters, motivation={"utility_weights": {}}
    )
    modalities = {o["modality"] for o in rec["utility"]["options"]}
    assert modalities == {"run"}


_RUN_ONLY_WEAK = {
    "critical_speed": _attr(estimate=4.0, unit="m/s", confidence=0.6),
    "velocity_at_vo2max": _attr(score="unknown", confidence=0.1),
    "run_fractional_utilization": _attr(score="unknown", confidence=0.1),
    "run_fatigue_resistance": _attr(score="unknown", confidence=0.1),
}


@pytest.mark.parametrize("sport", [None, SPORT_RUNNING], ids=["default", "explicit"])
def test_a_runner_with_no_confident_limiter_gets_a_running_fallback(sport):
    """The quiet path: the "neutral" week was a balanced *cycling* week.

    It arrives through the branch that fires when the model knows nothing, which
    is the branch nobody looks at — and `sport=None` is the **only** shape
    production uses (`routers/ai.py`, `schemas.py`, `freshness_allocation.py` all
    call it that way). An earlier version of this test passed the sport
    explicitly and so never touched the path that mattered; the parametrisation
    is there to stop that recurring.
    """
    rec = roi.recommend_training_roi(
        _RUN_ONLY_WEAK, ld.detect_limiters(_RUN_ONLY_WEAK), sport=sport
    )
    assert rec["sufficient"] is False
    assert rec["sport"] == SPORT_RUNNING
    assert all(g["system"].startswith("run_") for g in rec["expected_gain"])
    assert all(e["system"].startswith("run_") for e in rec["weekly_emphasis"])


def test_the_fallback_sport_comes_from_the_ranking_not_from_a_default():
    """Even the per-sport "nothing conclusive" entry names its sport."""
    limiters = ld.detect_limiters(_RUN_ONLY_WEAK)
    assert [c["limiter"] for c in limiters] == [ld.LIMITER_INSUFFICIENT]
    assert limiters[0]["sport"] == SPORT_RUNNING
    assert roi._sport_of_ranking(limiters) == SPORT_RUNNING


def test_a_sport_that_almost_cleared_the_gate_still_names_the_fallback():
    """A runner with a 0,25-confidence pace limiter is still a runner."""
    attrs = {
        "critical_speed": _attr(estimate=3.5, unit="m/s", confidence=0.3),
        "velocity_at_vo2max": _attr(estimate=4.5, unit="m/s", confidence=0.3),
        "run_fractional_utilization": _attr(estimate=3.5 / 4.5, confidence=0.3),
        "run_fatigue_resistance": _attr(score="high", confidence=0.3),
    }
    limiters = ld.detect_limiters(attrs)
    assert limiters[0]["confidence"] < ld.LIKELY_LIMITER_MIN_CONFIDENCE
    rec = roi.recommend_training_roi(attrs, limiters)
    assert rec["sufficient"] is False
    assert rec["sport"] == SPORT_RUNNING


@pytest.mark.parametrize(
    "limiters", [None, [], [{"limiter": "insufficient_data", "sport": None}]]
)
def test_a_ranking_that_names_no_sport_still_defaults_to_cycling(limiters):
    """The pre-#718 default, kept for the case with genuinely nothing to go on."""
    assert roi._sport_of_ranking(limiters) == SPORT_CYCLING
    assert roi.recommend_training_roi({}, limiters)["sport"] == SPORT_CYCLING


def test_an_existing_caller_passing_no_sport_is_unchanged_for_a_cyclist():
    attrs = _cycling(ftp=250, map_w=380, frac=0.66)
    limiters = ld.detect_limiters(attrs)
    rec = roi.recommend_training_roi(attrs, limiters)
    assert rec["limiter"] == ld.LIMITER_THRESHOLD
    assert rec["sport"] == SPORT_CYCLING
    assert [e["system"] for e in rec["weekly_emphasis"]] == [
        roi.SYSTEM_THRESHOLD,
        roi.SYSTEM_VO2MAX,
        roi.SYSTEM_ENDURANCE,
    ]


def test_an_existing_caller_passing_no_sport_still_gets_running_right():
    """"Whichever sport we are most sure about" has to mean it.

    Before #718 a running top limiter was simply absent from the gain table, so
    this call fell through to the cycling fallback — a runner being told to keep
    their bike periodization.
    """
    limiters = ld.detect_limiters(_RUN_LIMITED)
    rec = roi.recommend_training_roi(_RUN_LIMITED, limiters)
    assert rec["sufficient"] is True
    assert rec["sport"] == SPORT_RUNNING


def test_the_sport_is_read_off_the_limiter_not_off_the_argument():
    attrs = {**_cycling(ftp=250, map_w=380, frac=0.66), **_RUN_LIMITED}
    limiters = ld.detect_limiters(attrs)
    for sport in (SPORT_CYCLING, SPORT_RUNNING):
        rec = roi.recommend_training_roi(attrs, limiters, sport=sport)
        assert rec["sport"] == sport
        assert roi._SPORT_BY_LIMITER[rec["limiter"]] == sport


def test_one_recommendation_per_sport_the_athlete_trains():
    attrs = {**_cycling(ftp=250, map_w=380, frac=0.66), **_RUN_LIMITED}
    by_sport = roi.recommend_training_roi_by_sport(attrs, ld.detect_limiters(attrs))
    assert set(by_sport) == {SPORT_CYCLING, SPORT_RUNNING}
    assert by_sport[SPORT_CYCLING]["limiter"] == ld.LIMITER_THRESHOLD
    assert by_sport[SPORT_RUNNING]["limiter"] == ld.LIMITER_RUN_CRITICAL_SPEED


def test_a_cyclist_gets_a_one_entry_map_rather_than_an_invented_running_week():
    attrs = _cycling(ftp=250, map_w=380, frac=0.66)
    by_sport = roi.recommend_training_roi_by_sport(attrs, ld.detect_limiters(attrs))
    assert set(by_sport) == {SPORT_CYCLING}


def test_the_running_week_is_lighter_than_the_cycling_one():
    """#717's ceiling, applied where the recommendation is made.

    A recommendation that has to be capped by the plan gate downstream was the
    wrong recommendation.
    """
    run = roi.recommend_training_roi(_RUN_LIMITED, ld.detect_limiters(_RUN_LIMITED))
    cycling_attrs = _cycling(ftp=250, map_w=380, frac=0.66)
    ride = roi.recommend_training_roi(
        cycling_attrs, ld.detect_limiters(cycling_attrs)
    )
    assert sum(e["sessions"] for e in run["weekly_emphasis"]) < sum(
        e["sessions"] for e in ride["weekly_emphasis"]
    )


@pytest.mark.parametrize(
    "attrs,limiter",
    [
        (_running(cs=3.5, ceiling=4.5), ld.LIMITER_RUN_CRITICAL_SPEED),
        (_running(cs=4.3, ceiling=4.5), ld.LIMITER_RUN_SPEED_CEILING),
        (_running(fatigue="fades"), ld.LIMITER_RUN_DURABILITY),
    ],
    ids=["pace-limited", "ceiling-limited", "durability-limited"],
)
def test_every_running_limiter_has_a_rationale_that_speaks_in_paces(attrs, limiter):
    """All three, because a rationale nobody reaches is prose nobody checked."""
    limiters = ld.detect_limiters(attrs)
    rec = roi.recommend_training_roi(attrs, limiters, sport=SPORT_RUNNING)
    assert rec["limiter"] == limiter
    assert rec["hypothesis"]
    assert rec["rationale"]
    assert not _WATTS.search(rec["rationale"])
    assert "FTP" not in rec["rationale"] and "MAP" not in rec["rationale"]


def test_the_ceiling_rationale_warns_about_introducing_it_gradually():
    """A ceiling limiter prescribes the sessions running injuries come from."""
    attrs = _running(cs=4.3, ceiling=4.5)
    rec = roi.recommend_training_roi(attrs, ld.detect_limiters(attrs))
    assert "gradually" in rec["rationale"]


def test_a_running_rationale_survives_a_missing_estimate():
    """The prose degrades rather than disappearing.

    The limiter fires off the ratio; the rationale quotes the two speeds behind
    it, and a stored attribute can be malformed without the recommendation
    becoming empty.
    """
    attrs = {
        **_running(cs=3.5, ceiling=4.5),
        "critical_speed": _attr(estimate=None, score="unknown", confidence=0.6),
    }
    rec = roi.recommend_training_roi(attrs, ld.detect_limiters(attrs), sport=SPORT_RUNNING)
    assert rec["rationale"]
    assert "unknown pace" not in rec["rationale"]


def test_every_roi_system_has_a_label_and_a_race_specificity():
    """A system missing from either table scores on a silent default."""
    from services.training_utility import _SYSTEM_RACE_SPECIFICITY

    for gain_map in roi._GAIN_BY_LIMITER.values():
        for system in gain_map:
            assert system in roi._SYSTEM_LABEL
            assert system in _SYSTEM_RACE_SPECIFICITY


# ---------------------------------------------------------------------------
# Hypotheses: one per sport, never pooled
# ---------------------------------------------------------------------------


def test_a_runner_gets_a_running_hypothesis_carrying_its_sport():
    limiters = ld.detect_limiters(_RUN_LIMITED)
    hyps = he.derive_performance_hypotheses(_RUN_LIMITED, limiters)
    assert hyps
    top = hyps[0]
    assert top["sport"] == SPORT_RUNNING
    assert top["category"] == he.CATEGORY
    assert "running" in top["statement"]
    assert "/km" in " ".join(top["evidence"])
    assert top["alternative_explanations"]


def test_a_multisport_athlete_gets_a_limiter_hypothesis_for_each_sport():
    attrs = {**_cycling(ftp=250, map_w=380, frac=0.66), **_RUN_LIMITED}
    hyps = he.derive_performance_hypotheses(attrs, ld.detect_limiters(attrs))
    sports = [h["sport"] for h in hyps if "limiter is likely" in h["statement"]]
    assert sorted(sports) == [SPORT_CYCLING, SPORT_RUNNING]


def test_no_two_sports_ever_share_a_hypothesis_statement():
    """``propose_athlete_hypothesis`` merges by normalised statement per category.

    Two sports whose claims read alike would share one row and pool their
    evidence, which is exactly what #718 forbids. Distinct prose is the mechanism
    that keeps them apart, so it is asserted rather than assumed.
    """
    attrs = {
        **_cycling(ftp=250, map_w=380, frac=0.66, aerobic="developing", fatigue="fades"),
        **_running(cs=3.5, ceiling=4.5, fatigue="fades"),
    }
    statements = [
        h["statement"] for h in he.derive_performance_hypotheses(attrs, ld.detect_limiters(attrs))
    ]
    keys = [crud._normalise_hypothesis_key(s) for s in statements]
    assert len(set(keys)) == len(keys)


def test_the_standalone_cycling_claims_stay_cycling_only():
    """Both rest on ``aerobic_endurance``, which is cardiac drift on long rides.

    Borrowing it to make a claim about running would be the cross-sport pooling
    this issue exists to prevent.
    """
    attrs = {**_cycling(ftp=250, map_w=380, frac=0.66, aerobic="high"), **_RUN_LIMITED}
    hyps = he.derive_performance_hypotheses(attrs, ld.detect_limiters(attrs))
    standalone = [h for h in hyps if "limiter is likely" not in h["statement"]]
    assert standalone, "the aerobic-engine and FTP-underestimate claims should fire"
    assert all(h["sport"] == SPORT_CYCLING for h in standalone)


def test_a_cycling_only_athletes_hypotheses_are_unchanged():
    attrs = _cycling(ftp=250, map_w=380, frac=0.66, aerobic="high")
    hyps = he.derive_performance_hypotheses(attrs, ld.detect_limiters(attrs))
    assert hyps
    assert all(h["sport"] == SPORT_CYCLING for h in hyps)
    assert all("running" not in h["statement"] for h in hyps)


@pytest.mark.parametrize(
    "limiter",
    [
        ld.LIMITER_RUN_CRITICAL_SPEED,
        ld.LIMITER_RUN_SPEED_CEILING,
        ld.LIMITER_RUN_DURABILITY,
    ],
)
def test_a_running_hypothesis_is_withheld_when_its_evidence_is_missing(limiter: str):
    """No claim without the numbers behind it.

    The cycling builder has had this discipline since #479 — it returns ``None``
    rather than asserting a limiter it cannot cite — and the running one has to
    match, because the limiter can fire off a ratio whose components did not
    survive into the attribute map.
    """
    assert he._run_limiter_hypothesis(limiter, 0.6, {}) is None


def test_an_unrecognised_limiter_produces_no_running_hypothesis():
    assert he._run_limiter_hypothesis("something_new", 0.9, _RUN_LIMITED) is None


@pytest.mark.parametrize(
    "attrs,limiter",
    [
        (_running(cs=3.5, ceiling=4.5), ld.LIMITER_RUN_CRITICAL_SPEED),
        (_running(cs=4.3, ceiling=4.5), ld.LIMITER_RUN_SPEED_CEILING),
        (_running(fatigue="fades"), ld.LIMITER_RUN_DURABILITY),
    ],
    ids=["pace-limited", "ceiling-limited", "durability-limited"],
)
def test_every_running_limiter_produces_a_citable_hypothesis(attrs, limiter):
    limiters = ld.detect_limiters(attrs)
    assert ld.top_limiter_for_sport(limiters, SPORT_RUNNING) == limiter
    hyps = he.derive_performance_hypotheses(attrs, limiters)
    claim = next(h for h in hyps if h["sport"] == SPORT_RUNNING)
    assert "running limiter" in claim["statement"]
    assert claim["evidence"] and claim["alternative_explanations"]
    assert not _WATTS.search(" ".join(claim["evidence"]))


def test_a_limiter_below_the_confidence_gate_asserts_nothing():
    """Named at low confidence in the ranking, but not claimed as a hypothesis."""
    limiters = [
        {
            "limiter": ld.LIMITER_RUN_CRITICAL_SPEED,
            "sport": SPORT_RUNNING,
            "confidence": he._CONF_GATE - 0.01,
            "evidence": [],
            "counter_evidence": [],
        }
    ]
    assert ld.top_limiter_for_sport(limiters, SPORT_RUNNING) is None
    assert he.derive_performance_hypotheses(_RUN_LIMITED, limiters) == []


def test_the_hypothesis_gate_is_not_stricter_than_the_limiter_gate():
    """What makes the removed re-check safe, pinned so it stays safe.

    ``derive_performance_hypotheses`` trusts ``top_limiter_for_sport`` to have
    applied the confidence gate. It can only do that while the ranking's gate is
    at least as strict as its own; lowering ``LIKELY_LIMITER_MIN_CONFIDENCE``
    below ``_CONF_GATE`` would let a claim through that the engine considers too
    weak to assert, and nothing else would notice.
    """
    assert he._CONF_GATE <= ld.LIKELY_LIMITER_MIN_CONFIDENCE


@pytest.mark.asyncio
async def test_the_two_sports_persist_as_two_rows_with_their_own_evidence(
    db: AsyncSession,
):
    user = await crud.create_user(
        db, email="chain-two-sports@example.com", name="A", hashed_password="x"
    )
    attrs = {**_cycling(ftp=250, map_w=380, frac=0.66), **_RUN_LIMITED}
    limiters = ld.detect_limiters(attrs)
    await crud.upsert_athlete_performance_model(
        db,
        user.id,
        attributes=attrs,
        likely_limiter=ld.top_limiter(limiters),
        limiters=limiters,
        source_window_days=120,
        derived_from_rides=10,
    )
    count = await he.refresh_performance_hypotheses(
        db, user, now=datetime.now(timezone.utc)
    )
    assert count >= 2

    rows = list(await crud.list_athlete_hypotheses(db, user.id))
    model_rows = [r for r in rows if r.category == he.CATEGORY]
    by_sport: dict[str | None, list[models.AthleteHypothesis]] = {}
    for row in model_rows:
        by_sport.setdefault(row.sport, []).append(row)
    assert set(by_sport) == {SPORT_CYCLING, SPORT_RUNNING}
    assert all(r.evidence_count == 1 for r in model_rows)
    run_evidence = " ".join(by_sport[SPORT_RUNNING][0].evidence or [])
    assert "/km" in run_evidence and not _WATTS.search(run_evidence)


@pytest.mark.asyncio
async def test_a_writer_that_states_no_sport_does_not_erase_one(db: AsyncSession):
    """The weekly LLM pass states no sport. It must not blank what we knew.

    A caller omitting the field is not asserting the claim has no sport, so the
    column is set but never cleared.
    """
    user = await crud.create_user(
        db, email="chain-no-sport@example.com", name="A", hashed_password="x"
    )
    row = await crud.propose_athlete_hypothesis(
        db,
        user.id,
        statement="Running pace fades late in long runs.",
        category=he.CATEGORY,
        sport=SPORT_RUNNING,
    )
    assert row.sport == SPORT_RUNNING
    again = await crud.propose_athlete_hypothesis(
        db,
        user.id,
        statement="Running pace fades late in long runs.",
        category=he.CATEGORY,
    )
    assert again.id == row.id
    assert again.sport == SPORT_RUNNING


# ---------------------------------------------------------------------------
# The migration
# ---------------------------------------------------------------------------


def _load_migration():
    spec = importlib.util.spec_from_file_location("_hyp_sport_718", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sport_column(engine) -> bool:
    with engine.connect() as conn:
        return "sport" in {
            row[1]
            for row in conn.execute(
                sa.text("PRAGMA table_info(athlete_hypotheses)")
            ).all()
        }


def test_the_migration_applies_reverts_and_repeats(tmp_path):
    """Both directions, twice each — a redeploy replays them.

    The whole chain cannot be replayed on SQLite (a 2026-04 revision uses a
    PostgreSQL-only ``ALTER COLUMN TYPE``), so this exercises the one revision
    against a real table instead.
    """
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'hyp.db'}")
    models.AthleteHypothesis.__table__.create(engine)
    with engine.connect() as conn:
        conn.execute(sa.text("ALTER TABLE athlete_hypotheses DROP COLUMN sport"))
        conn.commit()
    assert _sport_column(engine) is False

    migration = _load_migration()
    for _ in range(2):
        with engine.connect() as conn:
            context = MigrationContext.configure(conn)
            with Operations.context(context):
                migration.upgrade()
            conn.commit()
        assert _sport_column(engine) is True

    for _ in range(2):
        with engine.connect() as conn:
            context = MigrationContext.configure(conn)
            with Operations.context(context):
                migration.downgrade()
            conn.commit()
        assert _sport_column(engine) is False
