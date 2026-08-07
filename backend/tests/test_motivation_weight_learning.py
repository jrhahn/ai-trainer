"""Learning the utility weights, and being able to say why (#566).

#563 already moved the weights from behaviour, but the rule was an if-chain and
the only trace was a log line. That is fine right up until the moment an athlete
looks at the balance in settings (#567), sees it different from last week, and
asks why — at which point an unexplained recommendation shift is undiagnosable.

Two things are under test here. The **rule**: declared as data, bounded, silent
until there is history to argue from, and blind to whatever the athlete pinned.
And the **trail**: one row per effective change, written at the same gate the
weights are, carrying which rule fired on what evidence.
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

NOW = datetime(2026, 8, 13, 9, 0, tzinfo=timezone.utc)


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
        db, email="weight-learning@example.com", name="Rider", hashed_password="x"
    )


def _swap_rides(user_id: str, count: int, *, first_id: int = 0) -> list[models.RideMetric]:
    """`count` rides where the plan said road and the athlete took the MTB."""
    return [
        models.RideMetric(
            user_id=user_id,
            strava_activity_id=first_id + i,
            activity_date=(NOW - timedelta(days=i)).date().isoformat(),
            sport_type="MountainBikeRide",
            plan_match_status="matched",
            matched_plan_snapshot={"sportType": "Ride"},
        )
        for i in range(count)
    ]


# ---------------------------------------------------------------------------
# The rule is written down
# ---------------------------------------------------------------------------


def test_every_rule_argues_only_about_real_components():
    """A typo in an effect key would otherwise be silently dropped by the gate."""
    for rule in mi.WEIGHT_RULES:
        assert rule.effects, f"{rule.name} argues for nothing"
        for component in rule.effects:
            assert component in mm.MOTIVATION_COMPONENTS


def test_every_rule_states_where_it_starts_and_where_it_saturates():
    for rule in mi.WEIGHT_RULES:
        assert rule.threshold >= 0
        assert rule.full_at >= rule.threshold
        # The signal is what the athlete is shown; an unnamed rule cannot explain
        # itself in the UI.
        assert rule.signal.strip()


def test_a_rule_is_silent_below_its_threshold_and_full_at_saturation():
    rule = mi.WeightRule(
        name="x", signal="s", threshold=0.2, full_at=0.4, effects={"enjoyment": 0.03}
    )

    assert rule.strength(0.19) == 0
    assert rule.strength(0.2) == 0
    assert rule.strength(0.3) == pytest.approx(0.5)
    assert rule.strength(0.4) == 1.0
    assert rule.strength(0.9) == 1.0


def test_the_rules_are_what_the_scorer_sums():
    """No arithmetic outside the declared rule set."""
    evidence = mi.summarize_behaviour(_swap_rides("u", 10), upcoming_races=2)

    summed: dict[str, float] = {}
    for activation in mi.evaluate_rules(evidence):
        for component, value in activation.effects.items():
            summed[component] = summed.get(component, 0.0) + value

    assert mi.score_behaviour(evidence) == summed


def test_a_stronger_signal_argues_further():
    weak = mi.summarize_behaviour(
        _swap_rides("u", 3) + [
            models.RideMetric(
                user_id="u",
                strava_activity_id=900 + i,
                activity_date=(NOW - timedelta(days=20 + i)).date().isoformat(),
                sport_type="Ride",
                plan_match_status="matched",
                matched_plan_snapshot={"sportType": "Ride"},
            )
            for i in range(7)
        ]
    )
    strong = mi.summarize_behaviour(_swap_rides("u", 10))

    assert mi.score_behaviour(strong)["enjoyment"] > mi.score_behaviour(weak)["enjoyment"]


# ---------------------------------------------------------------------------
# Cold start
# ---------------------------------------------------------------------------


async def test_a_brand_new_athlete_gets_the_defaults(db, user):
    """Not the example vector from #561 — that is one athlete's, not everyone's."""
    model = crud.motivation_model_as_dict(
        await crud.get_athlete_motivation_model(db, user.id)
    )

    assert model["utility_weights"] == mm.DEFAULT_WEIGHTS


def test_below_the_ride_floor_no_rule_fires():
    """Cold start is a floor on evidence, not a special case in the rules."""
    evidence = mi.summarize_behaviour(_swap_rides("u", mi.BEHAVIOUR_MIN_RIDES - 1))

    assert mi.evaluate_rules(evidence) == []
    assert mi.score_behaviour(evidence) == {}


def test_one_ride_above_the_floor_is_enough_to_start():
    evidence = mi.summarize_behaviour(_swap_rides("u", mi.BEHAVIOUR_MIN_RIDES))

    assert mi.evaluate_rules(evidence)


async def test_leaving_the_default_takes_repeated_runs(db, user):
    """How fast the vector may leave the cold start, pinned to a number.

    The strongest signal this system can observe — every ride a modality swap —
    still cannot move a weight by more than ``MAX_WEIGHT_NUDGE`` in one run. This
    asserts the floor on how long a change of direction takes to register, which
    is the property that keeps a fortnight of bad weather from redefining what
    the athlete trains for.
    """
    for ride in _swap_rides(user.id, 12):
        db.add(ride)
    await db.flush()

    baseline = mm.DEFAULT_WEIGHTS["enjoyment"]
    seen = [baseline]
    for run in range(4):
        await mi.refresh_motivation_from_behaviour(
            db, user, now=NOW + timedelta(weeks=run)
        )
        seen.append(
            crud.motivation_model_as_dict(
                await crud.get_athlete_motivation_model(db, user.id)
            )["utility_weights"]["enjoyment"]
        )

    steps = [round(b - a, 6) for a, b in zip(seen, seen[1:])]
    assert all(0 < step <= mm.MAX_WEIGHT_NUDGE + 1e-9 for step in steps), steps
    # And it is genuinely moving, not asymptotically stuck at the first step.
    assert seen[-1] > seen[1]


# ---------------------------------------------------------------------------
# What the athlete pinned is not learned
# ---------------------------------------------------------------------------


async def test_a_pinned_component_records_no_delta_however_hard_a_rule_argues(db, user):
    for ride in _swap_rides(user.id, 12):
        db.add(ride)
    await crud.upsert_athlete_motivation_model(
        db,
        user.id,
        updates={"pinned_weights": ["enjoyment"]},
        source=mm.SOURCE_USER_SET,
    )
    await db.flush()
    pinned_before = crud.motivation_model_as_dict(
        await crud.get_athlete_motivation_model(db, user.id)
    )["utility_weights"]["enjoyment"]

    await mi.refresh_motivation_from_behaviour(db, user, now=NOW)

    after = crud.motivation_model_as_dict(
        await crud.get_athlete_motivation_model(db, user.id)
    )["utility_weights"]
    assert after["enjoyment"] == pinned_before
    # The rule still fired — it is the gate that refuses, and the trail says so.
    events = await crud.get_motivation_weight_history(db, user.id)
    assert events
    assert "enjoyment" in events[0].pinned
    assert "enjoyment" not in events[0].deltas


# ---------------------------------------------------------------------------
# The audit trail
# ---------------------------------------------------------------------------


async def test_a_learning_run_records_which_evidence_moved_which_weight(db, user):
    for ride in _swap_rides(user.id, 12):
        db.add(ride)
    await db.flush()

    await mi.refresh_motivation_from_behaviour(db, user, now=NOW)

    events = await crud.get_motivation_weight_history(db, user.id)
    assert len(events) == 1
    event = events[0]

    assert event.source == mm.SOURCE_INFERRED
    assert event.deltas["enjoyment"] > 0
    assert event.weights_before != event.weights_after
    # Which rule, and what it saw.
    fired = {rule["rule"] for rule in event.rules}
    assert "modality_swap" in fired
    assert event.evidence["modality_swaps"] == 12
    assert event.evidence["rides"] == 12


async def test_the_recorded_delta_is_what_was_stored_not_what_was_asked_for(db, user):
    """The gate clamps and renormalizes; the trail has to show the outcome."""
    for ride in _swap_rides(user.id, 12):
        db.add(ride)
    await db.flush()

    await mi.refresh_motivation_from_behaviour(db, user, now=NOW)

    event = (await crud.get_motivation_weight_history(db, user.id))[0]
    for component, delta in event.deltas.items():
        expected = event.weights_after[component] - event.weights_before[component]
        assert delta == pytest.approx(expected, abs=1e-4)


async def test_an_athlete_edit_is_recorded_too_but_justifies_nothing(db, user):
    await crud.upsert_athlete_motivation_model(
        db,
        user.id,
        updates={"utility_weights": {"enjoyment": 0.6, "adaptation": 0.1}},
        source=mm.SOURCE_USER_SET,
    )
    await db.flush()

    event = (await crud.get_motivation_weight_history(db, user.id))[0]

    assert event.source == mm.SOURCE_USER_SET
    # Nothing was inferred, so there is nothing to justify — an empty rule list
    # would imply a learning run that found no signal, which is a different fact.
    assert event.rules is None
    assert event.evidence is None


async def test_a_write_that_leaves_the_weights_alone_records_nothing(db, user):
    """A trail full of "nothing changed" is a trail nobody reads."""
    await crud.upsert_athlete_motivation_model(
        db,
        user.id,
        updates={"primary_objective": "Ride the Trans-Alp"},
        source=mm.SOURCE_USER_SET,
    )
    await db.flush()

    assert await crud.get_motivation_weight_history(db, user.id) == []


async def test_the_history_reads_newest_first(db, user):
    for index in range(3):
        await crud.upsert_athlete_motivation_model(
            db,
            user.id,
            updates={"utility_weights": {"enjoyment": 0.3 + index * 0.1}},
            source=mm.SOURCE_USER_SET,
            now=NOW + timedelta(days=index),
        )
    await db.flush()

    events = await crud.get_motivation_weight_history(db, user.id)

    assert [e.recorded_at for e in events] == sorted(
        (e.recorded_at for e in events), reverse=True
    )


async def test_the_history_is_per_athlete(db, user):
    other = await crud.create_user(
        db, email="weight-learning-other@example.com", name="Other", hashed_password="x"
    )
    await crud.upsert_athlete_motivation_model(
        db, user.id, updates={"utility_weights": {"enjoyment": 0.6}},
        source=mm.SOURCE_USER_SET,
    )
    await db.flush()

    assert await crud.get_motivation_weight_history(db, other.id) == []


# ---------------------------------------------------------------------------
# The acceptance criterion from #566
# ---------------------------------------------------------------------------


async def test_an_mtb_rider_who_ignores_the_road_plan_ends_up_valuing_enjoyment(db, user):
    """"A simulated athlete who consistently rides MTB over prescribed road
    sessions ends up with a higher enjoyment weight" — and can find out why.
    """
    for week in range(12):
        for day in range(3):
            moment = NOW - timedelta(weeks=11 - week, days=day)
            db.add(
                models.RideMetric(
                    user_id=user.id,
                    strava_activity_id=1000 + week * 10 + day,
                    activity_date=moment.date().isoformat(),
                    sport_type="MountainBikeRide",
                    plan_match_status="matched",
                    matched_plan_snapshot={"sportType": "Ride"},
                )
            )
    await db.flush()

    for week in range(12):
        await mi.refresh_motivation_from_behaviour(
            db, user, now=NOW + timedelta(weeks=week)
        )

    weights = crud.motivation_model_as_dict(
        await crud.get_athlete_motivation_model(db, user.id)
    )["utility_weights"]

    assert weights["enjoyment"] > mm.DEFAULT_WEIGHTS["enjoyment"]
    assert sum(weights.values()) == pytest.approx(1.0)

    # And the reason is on record rather than in a log file that has rotated away.
    events = await crud.get_motivation_weight_history(db, user.id, limit=100)
    assert len(events) > 1
    assert all("modality_swap" in {r["rule"] for r in e.rules} for e in events)
