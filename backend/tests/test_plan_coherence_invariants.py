"""Property-based invariants for the coherence detectors.

``plan_coherence`` is deliberately detection-only: it reports that two
consecutive days hold the same session, and says nothing about what the second
one should become instead. That makes its contract unusually clean to state as
properties — a detector has exactly two ways to be wrong, and both are checkable
without deciding anything about coaching.

* **It must not see collisions that are not there.** A false positive hands the
  coach a fact that is not one, and the pipeline gate reverts a write over it.
* **It must not miss collisions that are.** A false negative is the 2026-09-07
  duplicate shipping again, which is the incident this module exists because of.

Around those sit the invariants that make the two meaningful: the verdict must
not depend on the order the plan happens to be in, on which spelling the writer
used for its keys, or on how a title was capitalised.

The spelling property is here as a regression guard rather than as a hypothesis.
Every reader in this module looked at camelCase alone until ai-trainer-ops#29, so
a plan written in snake_case produced no signature for any day: both detectors
returned empty and a plan full of duplicates was indistinguishable from a clean
one. Nothing was logged, because nothing was detected.
"""

from __future__ import annotations

from datetime import date, timedelta

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from services.plan_coherence import (
    DEFAULT_HORIZON_DAYS,
    collision_dates,
    collision_keys,
    find_repeated_sessions,
    find_stacked_strength,
    is_strength_day,
    session_signature,
)

TODAY = date(2026, 6, 15)
# Deliberately spans the horizon edges: two days before today, the whole window,
# and two days past it. Nothing about the detectors should be special-cased to
# the middle of the range.
DATES = [
    (TODAY + timedelta(days=offset)).isoformat()
    for offset in range(-2, DEFAULT_HORIZON_DAYS + 3)
]

# Small pools so Hypothesis produces genuine collisions constantly rather than
# wandering through a space where every session happens to be unique.
TITLES = ["", "Endurance ride", "Core and Upper Body", "Threshold 4x8"]
TYPES = ["rest", "off", "endurance", "intervals", "strength", "threshold"]
DURATIONS = [0, 45, 60, 90]

_SETTINGS = settings(
    max_examples=300,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


@st.composite
def days(draw, *, camel: bool = True) -> dict:
    day: dict = {"date": draw(st.sampled_from(DATES)), "slot": draw(st.integers(0, 1))}
    workout_type = draw(st.sampled_from(TYPES))
    duration = draw(st.sampled_from(DURATIONS))
    title = draw(st.sampled_from(TITLES))
    if camel:
        day["workoutType"] = workout_type
        day["durationMinutes"] = duration
    else:
        day["workout_type"] = workout_type
        day["duration_minutes"] = duration
    day["title"] = title
    return day


@st.composite
def plans(draw, *, camel: bool = True) -> list[dict]:
    return draw(st.lists(days(camel=camel), min_size=0, max_size=10))


def _to_snake(plan: list[dict]) -> list[dict]:
    out = []
    for day in plan:
        converted = {
            k: v for k, v in day.items() if k not in {"workoutType", "durationMinutes"}
        }
        converted["workout_type"] = day["workoutType"]
        converted["duration_minutes"] = day["durationMinutes"]
        out.append(converted)
    return out


def _expected_repeat_dates(plan: list[dict], today: str | None) -> set[tuple[str, str]]:
    """An independent oracle for which date pairs collide.

    Reuses ``session_signature`` — what counts as "the same session" is the
    definition under test's own vocabulary, not something to reimplement — but
    does the grouping, adjacency and horizon arithmetic separately, which is the
    part that can actually be wrong.
    """
    start = date.fromisoformat(today) if today else None
    by_date: dict[date, set] = {}
    for day in plan:
        try:
            parsed = date.fromisoformat(str(day.get("date")))
        except (TypeError, ValueError):
            continue
        if start is not None and not (0 <= (parsed - start).days <= DEFAULT_HORIZON_DAYS):
            continue
        signature = session_signature(day)
        if signature is None:
            continue
        by_date.setdefault(parsed, set()).add(signature)
    pairs = set()
    for current, signatures in by_date.items():
        following = by_date.get(current + timedelta(days=1))
        if following and signatures & following:
            pairs.add((current.isoformat(), (current + timedelta(days=1)).isoformat()))
    return pairs


# --------------------------------------------------------------------------
# Soundness: no collisions invented, none missed
# --------------------------------------------------------------------------


@_SETTINGS
@given(plan=plans())
def test_every_reported_repeat_is_a_real_one(plan):
    """No false positives: a reported pair is two adjacent dates sharing a session."""
    expected = _expected_repeat_dates(plan, TODAY.isoformat())
    for repeat in find_repeated_sessions(plan, TODAY.isoformat()):
        earlier, later = repeat["dates"]
        assert (earlier, later) in expected, f"invented a collision: {repeat}"
        assert date.fromisoformat(later) - date.fromisoformat(earlier) == timedelta(
            days=1
        ), f"reported non-adjacent dates: {repeat}"


@_SETTINGS
@given(plan=plans())
def test_no_real_repeat_is_missed(plan):
    """No false negatives: this is the 2026-09-07 duplicate shipping again."""
    expected = _expected_repeat_dates(plan, TODAY.isoformat())
    reported = {
        tuple(r["dates"]) for r in find_repeated_sessions(plan, TODAY.isoformat())
    }
    assert expected <= reported, f"missed collisions: {expected - reported}"


@_SETTINGS
@given(plan=plans())
def test_non_loading_days_are_never_reported(plan):
    """Two rest days in a row is a taper, and an untitled day is not evidence."""
    for repeat in find_repeated_sessions(plan, TODAY.isoformat()):
        assert str(repeat["workoutType"]).strip().lower() not in {"rest", "off", ""}
        assert str(repeat["title"] or "").strip() != ""
        assert repeat["durationMinutes"] not in (0, "0")


@_SETTINGS
@given(day=days())
def test_a_single_date_never_collides_with_itself(day):
    """Two-a-days are a deliberate pattern, not a scheduling slip.

    The same session twice on one date — both slots — must not read as a repeat,
    which is only true because the comparison is across dates.
    """
    twice = [day, {**day, "slot": day["slot"] + 1}]
    assert find_repeated_sessions(twice, TODAY.isoformat()) == []


# --------------------------------------------------------------------------
# The verdict must not depend on how the plan was written
# --------------------------------------------------------------------------


@_SETTINGS
@given(plan=plans())
def test_detection_does_not_depend_on_key_spelling(plan):
    """Regression guard for ai-trainer-ops#29.

    Before the fix both detectors returned empty for any snake_case plan, so a
    plan full of duplicates looked exactly like a clean one — a silent false
    negative in the module written to stop a duplicate reaching production.
    """
    snake = _to_snake(plan)
    camel_repeats = {tuple(r["dates"]) for r in find_repeated_sessions(plan, TODAY.isoformat())}
    snake_repeats = {tuple(r["dates"]) for r in find_repeated_sessions(snake, TODAY.isoformat())}
    assert camel_repeats == snake_repeats

    camel_stacks = {tuple(s["dates"]) for s in find_stacked_strength(plan, TODAY.isoformat())}
    snake_stacks = {tuple(s["dates"]) for s in find_stacked_strength(snake, TODAY.isoformat())}
    assert camel_stacks == snake_stacks


@_SETTINGS
@given(plan=plans(), data=st.data())
def test_detection_does_not_depend_on_plan_order(plan, data):
    """A plan is a set of sessions; the list order it arrives in is incidental."""
    shuffled = data.draw(st.permutations(plan))
    assert collision_keys(plan, TODAY.isoformat()) == collision_keys(
        shuffled, TODAY.isoformat()
    )


@_SETTINGS
@given(day=days())
def test_signature_ignores_capitalisation_and_padding(day):
    """``strip().lower()`` has to apply to both halves of the comparison."""
    noisy = {**day, "title": f"  {str(day['title']).upper()}  "}
    assert session_signature(noisy) == session_signature(day)


# --------------------------------------------------------------------------
# Horizon
# --------------------------------------------------------------------------


@_SETTINGS
@given(plan=plans())
def test_nothing_before_today_or_past_the_horizon_is_reported(plan):
    """A duplicate already ridden past is history; one a month out is noise."""
    limit = TODAY + timedelta(days=DEFAULT_HORIZON_DAYS)
    for repeat in find_repeated_sessions(plan, TODAY.isoformat()):
        for raw in repeat["dates"]:
            parsed = date.fromisoformat(raw)
            assert TODAY <= parsed <= limit + timedelta(days=1), (
                f"reported {raw}, outside [{TODAY}, {limit}]"
            )


# --------------------------------------------------------------------------
# Stacked strength, and the shared key space
# --------------------------------------------------------------------------


@_SETTINGS
@given(plan=plans())
def test_stacked_strength_only_reports_adjacent_strength_days(plan):
    """Distinct from a repeat: the two sessions may differ, both must be strength."""
    by_date = {}
    for day in plan:
        by_date.setdefault(str(day.get("date")), []).append(day)
    for stack in find_stacked_strength(plan, TODAY.isoformat()):
        earlier, later = stack["dates"]
        assert date.fromisoformat(later) - date.fromisoformat(earlier) == timedelta(days=1)
        for raw in (earlier, later):
            assert any(is_strength_day(d) for d in by_date.get(raw, [])), (
                f"{raw} reported as stacked strength but holds none: {by_date.get(raw)}"
            )


@_SETTINGS
@given(plan=plans())
def test_collision_keys_account_for_every_finding(plan):
    """The gate compares key sets before and after a write, so the map must be total.

    A finding with no key is a collision the gate cannot see; a key spanning
    non-adjacent dates would send it to revert the wrong day.
    """
    today = TODAY.isoformat()
    keys = collision_keys(plan, today)
    repeats = find_repeated_sessions(plan, today)
    stacks = find_stacked_strength(plan, today)

    kinds = [key[0] for key in keys]
    assert len(keys) == len(set(keys)), "keys must be unique"
    assert kinds.count("strength-stacked") == len(
        {tuple(s["dates"]) for s in stacks}
    ), "every stacked-strength finding needs exactly one key"
    assert kinds.count("repeat") == len(
        {
            (tuple(r["dates"]), r["workoutType"], r["title"], r["durationMinutes"])
            for r in repeats
        }
    ), "every distinct repeat needs exactly one key"

    for key in keys:
        earlier, later = collision_dates(key)
        assert date.fromisoformat(later) - date.fromisoformat(earlier) == timedelta(
            days=1
        ), f"key spans non-adjacent dates: {key}"
