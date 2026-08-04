"""The coach prompt's cacheable prefix (#514, #538).

Gemini's implicit cache bills a repeated prefix at a tenth of the input rate,
but only matches from the very first token: one varying character anywhere in
the head and the whole request pays full price. #514 reordered the prompt to
earn that discount and had no way to check the result — ``prompt_sha`` hashes
the *entire* system prompt, which contains today's date and the plan, so it
varies on every call by construction and can never show whether the prefix
held.

These tests do that job deterministically, which is the only place it can be
done: production tells us what the model charged, never why.
"""

from __future__ import annotations

from services.prompts import ask_trainer_system, coach_static_prefix


def _render(seed: int, *, sparse: bool = False) -> str:
    """A full coach prompt for a distinct athlete on a distinct day.

    ``sparse`` drops every optional section, since a section that appears only
    for some athletes would break the prefix for everyone if it were placed
    inside it.
    """
    return ask_trainer_system(
        profile={"name": f"Athlete{seed}", "ftp": 250 + seed},
        today=f"2026-08-0{seed}",
        last_7_days=[{"date": f"2026-07-2{seed}", "tss": 60 + seed}],
        next_n_days=[{"date": f"2026-08-0{seed}", "workoutType": "endurance"}],
        assessment_section=f"\n\nAssessment {seed}",
        memory_section=f"\n\nCoach memory: note {seed}",
        workout_section=f"\n\nWorkout {seed}",
        plan_updates_rule='\n- "planUpdates": array',
        athlete_context=None if sparse else {"goal": f"g{seed}"},
        athlete_memory_facts=None if sparse else [{"fact": f"f{seed}"}],
        athlete_model=None if sparse else {"summary": f"m{seed}"},
        open_questions=None if sparse else [{"question": f"q{seed}"}],
        pending_inquiries=None if sparse else [{"question": f"i{seed}"}],
        performance_model=None if sparse else {"limiter": f"l{seed}"},
        performance_recommendation=None if sparse else {"action": f"a{seed}"},
        hypotheses=None if sparse else [{"statement": f"h{seed}"}],
        science_context="" if sparse else f"paper {seed}",
        training_load=None if sparse else {"ctl": 50, "atl": 40, "tsb": -10},
        classification=None if sparse else {"category": f"c{seed}"},
        metrics_history_section="" if sparse else f"Ride metrics {seed}",
        race_events_section="" if sparse else f"Races {seed}",
        weather_context_section="" if sparse else f"Weather {seed}",
        training_status_badge=None if sparse else ("Building", f"r{seed}", "green"),
        date_context=f"Current local date context: 2026-08-0{seed}",
    )


def test_the_prefix_is_identical_for_different_athletes_on_different_days():
    assert coach_static_prefix() == coach_static_prefix()
    rich, sparse = _render(1), _render(2, sparse=True)
    prefix = coach_static_prefix()
    assert rich.startswith(prefix)
    assert sparse.startswith(prefix)


def test_nothing_volatile_is_placed_ahead_of_the_prefix():
    """A cache prefix matches from token zero, so the head has to be the head."""
    assert _render(1).index(coach_static_prefix()) == 0


def test_the_prompts_diverge_only_after_the_prefix():
    """Guards the boundary itself, not just its two ends.

    If a volatile value were placed *inside* the static block, the two renders
    would part company before the block ended and the discount would be lost
    without any test failing on a startswith assertion alone.
    """
    rich, sparse = _render(1), _render(2, sparse=True)
    shared = 0
    for a, b in zip(rich, sparse):
        if a != b:
            break
        shared += 1
    assert shared >= len(coach_static_prefix())


def test_the_prefix_stays_above_the_implicit_cache_minimum():
    """Below the model's minimum request size, nothing is cached at all.

    Google documents 4,096 tokens for the current Flash generation. The live
    API counted this prefix at 4,270 tokens for 20,521 characters — 4.8
    chars/token, not the usual rule-of-thumb 4 — so the floor is set from the
    measurement rather than the estimate, and sits just under today's length.

    The coach model (``flash-lite``) offers no context caching at all, so this
    currently guards a discount we cannot collect. It is kept because the
    threshold is the thing a prompt diet would silently cross, and the model is
    one env variable away from changing.
    """
    assert len(coach_static_prefix()) >= 20_000


def test_the_prefix_carries_no_athlete_data():
    """Cheap smell test for the mistake this whole file exists to catch.

    Only the labels that introduce *data* — the rules are allowed to name the
    sections they point at, and deliberately do (#514).
    """
    prefix = coach_static_prefix()
    for volatile in (
        "Athlete profile:",
        "Today's date:",
        "Last 7 days of training",
        "Upcoming plan (",
    ):
        assert volatile not in prefix
