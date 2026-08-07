"""Learning what the athlete trains for, from what they say and do (#563).

#562 gave motivation a home; this covers what fills it. The tests are organised
around the property that actually matters — **restraint**. Extraction is easy;
the risk is a coach that redefines an athlete's goal because of one sentence, or
that quietly overrules a goal the athlete stated by hand.

So the assertions come in pairs throughout: the signal is picked up, *and* one
occurrence of it is not enough to act on.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
from services import motivation_inference as mi
from services import motivation_model as mm
from tests.conftest import TestSessionLocal


NOW = datetime(2026, 8, 11, 9, 0, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def db() -> AsyncSession:
    async with TestSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@pytest_asyncio.fixture
async def user(db: AsyncSession) -> models.User:
    return await crud.create_user(
        db, email="motivation-inference@example.com", name="Rider", hashed_password="x"
    )


# ---------------------------------------------------------------------------
# Extraction: the statements from #561
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "component", "direction"),
    [
        ("Honestly, I don't care about races.", "race_performance", -1),
        ("I'm not interested in racing at all.", "race_performance", -1),
        ("I want more trail time this summer.", "enjoyment", +1),
        ("I'd rather ride the MTB than the road bike.", "enjoyment", +1),
        ("I train to enjoy riding, that's it.", "enjoyment", +1),
        ("I don't chase FTP any more.", "adaptation", -1),
        ("I want to stay healthy above all.", "health", +1),
        ("I just want to be consistent.", "consistency", +1),
    ],
)
def test_the_example_statements_argue_in_the_right_direction(
    message, component, direction
):
    """Every statement named in #561 has to be readable, and read correctly."""
    signals = mi.extract_motivation_statements(message)

    assert signals, f"no signal extracted from {message!r}"
    nudges = {}
    for signal in signals:
        for key, delta in signal.weight_nudges.items():
            nudges[key] = nudges.get(key, 0.0) + delta

    assert component in nudges
    assert nudges[component] * direction > 0


def test_german_statements_are_read_too():
    """The athlete writes to their coach in whichever language they think in."""
    signals = mi.extract_motivation_statements("Ich will mehr Trails fahren.")

    assert any(s.objective for s in signals)


def test_a_free_form_goal_is_captured_verbatim():
    signals = mi.extract_motivation_statements(
        "My goal is to ride the Trans-Alp next summer without suffering."
    )

    objectives = [s.objective for s in signals if s.objective]
    assert objectives == ["Ride the Trans-Alp next summer without suffering"]


def test_ordinary_chat_extracts_nothing():
    """The common case: a message about a ride is not a statement about a life."""
    assert mi.extract_motivation_statements("How did my threshold session look?") == []
    assert mi.extract_motivation_statements("") == []


# ---------------------------------------------------------------------------
# Capture: traceable, and bounded
# ---------------------------------------------------------------------------


async def test_a_statement_is_stored_with_the_athletes_own_words(db, user):
    message = "I want more trail time — my legs feel great lately."
    await mi.capture_motivation_from_message(db, user.id, message, now=NOW)

    stored = crud.motivation_model_as_dict(
        await crud.get_athlete_motivation_model(db, user.id)
    )
    entry = stored["secondary_objectives"][0]

    assert entry["text"] == "Maximize time on technical trails"
    # The snippet is what makes an inferred objective correctable rather than
    # mysterious (#567).
    assert entry["source_snippet"] == message
    assert entry["source"] == mm.SOURCE_INFERRED


async def test_one_statement_does_not_become_the_primary_objective(db, user):
    """The bar for "this is what you train for" is higher than one sentence."""
    await mi.capture_motivation_from_message(
        db, user.id, "I want more trail time.", now=NOW
    )

    stored = crud.motivation_model_as_dict(
        await crud.get_athlete_motivation_model(db, user.id)
    )

    assert stored["primary_objective"] == ""
    assert stored["secondary_objectives"][0]["confidence"] <= mm.INITIAL_CONFIDENCE_CAP


async def test_a_repeated_statement_earns_promotion_to_primary(db, user):
    """Recurrence is what turns a remark into the objective the planner uses."""
    for day in range(3):
        await mi.capture_motivation_from_message(
            db,
            user.id,
            "I want more trail time.",
            now=NOW + timedelta(days=day * 7),
        )

    stored = crud.motivation_model_as_dict(
        await crud.get_athlete_motivation_model(db, user.id)
    )

    assert stored["primary_objective"] == "Maximize time on technical trails"
    assert stored["primary_objective_confidence"] >= mm.PROMOTION_MIN_CONFIDENCE
    assert stored["primary_objective_snippet"]


async def test_a_statement_moves_weights_only_a_little(db, user):
    before = mm.DEFAULT_WEIGHTS["race_performance"]

    await mi.capture_motivation_from_message(
        db, user.id, "I don't care about races.", now=NOW
    )
    stored = crud.motivation_model_as_dict(
        await crud.get_athlete_motivation_model(db, user.id)
    )

    after = stored["utility_weights"]["race_performance"]
    assert after < before
    assert before - after <= mm.MAX_WEIGHT_NUDGE + 1e-9
    assert sum(stored["utility_weights"].values()) == pytest.approx(1.0)


async def test_a_hand_set_objective_survives_a_capture(db, user):
    """The clobber guard, exercised through the real inference path (#342/#345/#346)."""
    await crud.upsert_athlete_motivation_model(
        db,
        user.id,
        updates={"primary_objective": "Ride every trail in the Black Forest"},
        source=mm.SOURCE_USER_SET,
    )

    for _ in range(5):
        await mi.capture_motivation_from_message(
            db, user.id, "I want to win my A-race this year.", now=NOW
        )

    stored = crud.motivation_model_as_dict(
        await crud.get_athlete_motivation_model(db, user.id)
    )
    assert stored["primary_objective"] == "Ride every trail in the Black Forest"
    assert stored["primary_objective_source"] == mm.SOURCE_USER_SET


async def test_a_pinned_weight_survives_a_capture(db, user):
    await crud.upsert_athlete_motivation_model(
        db,
        user.id,
        updates={
            "utility_weights": {"race_performance": 0.3},
            "pinned_weights": ["race_performance"],
        },
        source=mm.SOURCE_USER_SET,
    )
    pinned_before = crud.motivation_model_as_dict(
        await crud.get_athlete_motivation_model(db, user.id)
    )["utility_weights"]["race_performance"]

    await mi.capture_motivation_from_message(
        db, user.id, "I don't care about races.", now=NOW
    )

    stored = crud.motivation_model_as_dict(
        await crud.get_athlete_motivation_model(db, user.id)
    )
    assert stored["utility_weights"]["race_performance"] == pytest.approx(pinned_before)


async def test_a_message_with_no_motivation_writes_nothing(db, user):
    assert (
        await mi.capture_motivation_from_message(
            db, user.id, "What should I do tomorrow?", now=NOW
        )
        is None
    )
    assert await crud.get_athlete_motivation_model(db, user.id) is None


# ---------------------------------------------------------------------------
# Behaviour
# ---------------------------------------------------------------------------


def _ride(
    *,
    date: str,
    sport: str,
    planned: str | None = None,
    matched: bool = True,
) -> models.RideMetric:
    return models.RideMetric(
        user_id="u",
        strava_activity_id=abs(hash((date, sport, planned))) % 10**9,
        activity_date=date,
        sport_type=sport,
        plan_match_status="matched" if matched else "unmatched",
        matched_plan_snapshot={"sportType": planned} if planned else None,
    )


def test_riding_the_mtb_when_the_plan_said_road_is_counted_as_a_swap():
    rides = [
        _ride(date="2026-08-0%d" % (i + 1), sport="MountainBikeRide", planned="Ride")
        for i in range(9)
    ]

    evidence = mi.summarize_behaviour(rides)

    assert evidence.modality_swaps == 9
    assert mi.score_behaviour(evidence)["enjoyment"] > 0


def test_following_the_plan_argues_for_structure():
    rides = [
        _ride(date="2026-08-0%d" % (i + 1), sport="Ride", planned="Ride")
        for i in range(9)
    ]

    nudges = mi.score_behaviour(mi.summarize_behaviour(rides))

    assert nudges["consistency"] > 0
    assert nudges["adaptation"] > 0
    assert "enjoyment" not in nudges


def test_too_little_history_argues_nothing():
    """Below the floor there is no pattern, only rides."""
    rides = [_ride(date="2026-08-01", sport="MountainBikeRide", planned="Ride")]

    assert mi.score_behaviour(mi.summarize_behaviour(rides)) == {}


async def test_behaviour_shifts_the_weights_over_several_weeks(db, user):
    """The acceptance criterion from #563: a sustained signal moves the vector.

    One run must not, and twelve weekly runs must — that gap is the whole design.
    """
    for week in range(12):
        for day in range(3):
            moment = NOW - timedelta(weeks=11 - week, days=day)
            db.add(
                models.RideMetric(
                    user_id=user.id,
                    strava_activity_id=week * 10 + day,
                    activity_date=moment.date().isoformat(),
                    sport_type="MountainBikeRide",
                    plan_match_status="matched",
                    matched_plan_snapshot={"sportType": "Ride"},
                )
            )
    await db.flush()

    baseline = mm.DEFAULT_WEIGHTS["enjoyment"]

    await mi.refresh_motivation_from_behaviour(db, user, now=NOW)
    after_one = crud.motivation_model_as_dict(
        await crud.get_athlete_motivation_model(db, user.id)
    )["utility_weights"]["enjoyment"]

    assert after_one > baseline
    assert after_one - baseline <= mm.MAX_WEIGHT_NUDGE + 1e-9

    for week in range(11):
        await mi.refresh_motivation_from_behaviour(
            db, user, now=NOW + timedelta(weeks=week + 1)
        )

    final = crud.motivation_model_as_dict(
        await crud.get_athlete_motivation_model(db, user.id)
    )["utility_weights"]

    assert final["enjoyment"] > after_one
    assert final["enjoyment"] > baseline + mm.MAX_WEIGHT_NUDGE
    assert sum(final.values()) == pytest.approx(1.0)


async def test_behaviour_never_moves_a_pinned_weight(db, user):
    for day in range(9):
        db.add(
            models.RideMetric(
                user_id=user.id,
                strava_activity_id=500 + day,
                activity_date=(NOW - timedelta(days=day)).date().isoformat(),
                sport_type="MountainBikeRide",
                plan_match_status="matched",
                matched_plan_snapshot={"sportType": "Ride"},
            )
        )
    await db.flush()
    await crud.upsert_athlete_motivation_model(
        db,
        user.id,
        updates={"utility_weights": {"enjoyment": 0.4}, "pinned_weights": ["enjoyment"]},
        source=mm.SOURCE_USER_SET,
    )
    pinned = crud.motivation_model_as_dict(
        await crud.get_athlete_motivation_model(db, user.id)
    )["utility_weights"]["enjoyment"]

    await mi.refresh_motivation_from_behaviour(db, user, now=NOW)

    stored = crud.motivation_model_as_dict(
        await crud.get_athlete_motivation_model(db, user.id)
    )
    assert stored["utility_weights"]["enjoyment"] == pytest.approx(pinned)


async def test_an_athlete_with_no_rides_is_left_alone(db, user):
    assert await mi.refresh_motivation_from_behaviour(db, user, now=NOW) == 0
    assert await crud.get_athlete_motivation_model(db, user.id) is None


def test_what_the_athlete_actually_rides_moves_modality_affinity():
    """Revealed preference — the one currency that cannot be talked up (#564)."""
    rides = [
        _ride(date="2026-08-%02d" % (i + 1), sport="MountainBikeRide", planned="Ride")
        for i in range(10)
    ]

    nudges = mi.score_modality_affinity(mi.summarize_behaviour(rides))

    assert nudges["mtb"] > 0
    assert nudges["road"] < 0


def test_a_balanced_spread_argues_for_nothing():
    rides = [
        _ride(date="2026-08-%02d" % (i + 1), sport=sport, planned=sport)
        for i, sport in enumerate(
            ["Ride", "MountainBikeRide", "GravelRide", "VirtualRide"] * 2
        )
    ]

    nudges = mi.score_modality_affinity(mi.summarize_behaviour(rides))

    assert all(abs(value) < 1e-9 for value in nudges.values())


def test_gym_affinity_is_not_inferred_from_a_cycling_feed():
    """Silence in the ride data is not evidence of dislike."""
    rides = [
        _ride(date="2026-08-%02d" % (i + 1), sport="Ride", planned="Ride")
        for i in range(10)
    ]

    assert "gym" not in mi.score_modality_affinity(mi.summarize_behaviour(rides))


async def test_behaviour_persists_the_modality_affinity(db, user):
    for day in range(10):
        db.add(
            models.RideMetric(
                user_id=user.id,
                strava_activity_id=900 + day,
                activity_date=(NOW - timedelta(days=day)).date().isoformat(),
                sport_type="MountainBikeRide",
                plan_match_status="matched",
                matched_plan_snapshot={"sportType": "Ride"},
            )
        )
    await db.flush()

    await mi.refresh_motivation_from_behaviour(db, user, now=NOW)

    stored = crud.motivation_model_as_dict(
        await crud.get_athlete_motivation_model(db, user.id)
    )
    assert stored["modality_affinity"]["mtb"] > mm.NEUTRAL_AFFINITY
    assert stored["modality_affinity"]["road"] < mm.NEUTRAL_AFFINITY
    # Independent scores, not a distribution.
    assert sum(stored["modality_affinity"].values()) != pytest.approx(1.0)


async def test_saying_you_prefer_the_mtb_moves_the_affinity(db, user):
    await mi.capture_motivation_from_message(
        db, user.id, "I'd rather ride the MTB than the road bike.", now=NOW
    )

    stored = crud.motivation_model_as_dict(
        await crud.get_athlete_motivation_model(db, user.id)
    )
    assert stored["modality_affinity"]["mtb"] > mm.NEUTRAL_AFFINITY


# ---------------------------------------------------------------------------
# Contradictions: raised, not resolved
# ---------------------------------------------------------------------------


def test_a_racing_objective_with_no_race_on_the_calendar_is_flagged():
    model = mm.normalize_model(
        {"secondary_objectives": [{"text": "Perform at races", "confidence": 0.7}]}
    )
    evidence = mi.BehaviourEvidence(rides=20, upcoming_races=0)

    flagged = mi.detect_behaviour_contradictions(model, evidence)

    assert len(flagged) == 1
    assert flagged[0]["status"] == mm.STATUS_CONTRADICTED
    # The tension is put to the athlete rather than settled by the coach (#567).
    assert flagged[0]["contradiction_note"]
    assert flagged[0]["text"] == "Perform at races"


def test_a_racing_objective_backed_by_a_race_is_not_flagged():
    model = mm.normalize_model(
        {"secondary_objectives": [{"text": "Perform at races", "confidence": 0.7}]}
    )
    evidence = mi.BehaviourEvidence(rides=20, upcoming_races=2)

    assert mi.detect_behaviour_contradictions(model, evidence) == []


async def test_restating_a_contradicted_objective_clears_the_flag(db, user):
    """Fresh evidence answers the question, the same way a memory fact revives.

    The athlete restating it also carries it over the promotion bar, so the
    objective ends up as the primary rather than as a cleared secondary — which
    is the point: they answered, and the answer was "yes, still this".
    """
    await crud.upsert_athlete_motivation_model(
        db,
        user.id,
        updates={
            "secondary_objectives": [
                {
                    "text": "Perform at races",
                    "confidence": 0.4,
                    "status": "contradicted",
                    "contradiction_note": "No race on your calendar.",
                }
            ]
        },
        source=mm.SOURCE_INFERRED,
    )

    await mi.capture_motivation_from_message(
        db, user.id, "I want to win my A-race this year.", now=NOW
    )

    stored = crud.motivation_model_as_dict(
        await crud.get_athlete_motivation_model(db, user.id)
    )

    assert stored["primary_objective"] == "Perform at races"
    assert not any(
        entry["status"] == mm.STATUS_CONTRADICTED
        for entry in stored["secondary_objectives"]
    )
