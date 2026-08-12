"""The numbers this codebase decides with, and what fails when they move (#600).

"Rules as data" is the house style — `VALUE_RULES`, `WEIGHT_RULES`,
`SIGNAL_RULES` — and the constants beside those tables are the other half of the
policy. `CHANNEL_COST` decides which uncertainties are ever put to the athlete.
`DEFAULT_LOAD_PER_HOUR` decides whether an hour in the gym raises TSB.
`ENDURANCE_SPLIT_SESSION_MAX_GAP_SECONDS` decides whether two rides were one
session. A passing suite says nothing about whether any of them is load-bearing:
a test can pass because the behaviour is right, or because it asserts on
something that would be true whatever the number said.

This is the list of the ones that are claimed to matter, with the perturbation
that has to be noticed and the tests that own it. `scripts/check_policy_guards.py`
runs it. A number that survives its perturbation is either not policy or not
tested.

**A `reason` instead of a `moved_to` is not a loophole — it is the finding.** It
records a constant nobody could show a test for, so the gap is on the record
rather than absent from it. Keep the list short and keep each reason specific.

Perturbations are chosen to be *wrong*, not merely different: far enough that a
test which genuinely depends on the number cannot miss it. A test that only
fails on a hair's-width change would be asserting the constant back at itself.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PolicyGuard:
    """One number, what it decides, and how we know something depends on it."""

    target: str
    # What this number decides, in one line — the reason it is on this list.
    decides: str
    tests: tuple[str, ...]
    # What to move it to. ``None`` records a constant we could not show a guard
    # for; ``reason`` then has to say what is missing.
    moved_to: str | None = None
    reason: str = ""

    @property
    def spec(self) -> str:
        return f"{self.target}={self.moved_to}"


GUARDS: tuple[PolicyGuard, ...] = (
    # --- What the coach is allowed to ask (#582, #593) ----------------------
    PolicyGuard(
        target="services.uncertainty_value:CHANNEL_COST[inquiry]",
        decides="how much an uncertainty must be worth before the athlete is asked",
        moved_to="0.95",
        tests=("tests/test_uncertainty_value.py",),
    ),
    PolicyGuard(
        target="services.uncertainty_value:CHANNEL_DATA_DISCOUNT[open_question]",
        decides=(
            "whether an open question is penalised for being answerable by data "
            "— the distinction the whole gate turns on"
        ),
        moved_to="1.0",
        tests=("tests/test_uncertainty_value.py",),
    ),
    PolicyGuard(
        target="services.uncertainty_value:NEUTRAL_RELEVANCE",
        decides="what an uncertainty is worth when no rule recognises it",
        moved_to="0.0",
        tests=("tests/test_uncertainty_value.py",),
    ),
    PolicyGuard(
        target="services.uncertainty_value:EXCLUSIVE_KNOWLEDGE_BONUS",
        decides="whether recognising that only the athlete knows something helps",
        moved_to="0.0",
        tests=("tests/test_uncertainty_value.py",),
    ),
    PolicyGuard(
        target="services.workout_curiosity:EXPLAINS_THE_NUMBERS_BONUS",
        decides="whether a candidate explanation outranks an equally valuable topic",
        moved_to="1.0",
        tests=("tests/test_workout_curiosity.py",),
    ),
    PolicyGuard(
        target="services.workout_curiosity:HR_LATE_JUMP_RATIO",
        decides="when a late heart-rate step counts as the unusual thing to name",
        moved_to="99.0",
        tests=("tests/test_workout_curiosity.py",),
    ),
    PolicyGuard(
        target="services.workout_curiosity:RISING_POWER_MIN_STEPS",
        decides="how many efforts make a rising profile rather than noise",
        moved_to="9",
        tests=("tests/test_workout_curiosity.py",),
    ),
    # --- What a session cost (#579, #591) -----------------------------------
    PolicyGuard(
        target="services.training_load:DEFAULT_LOAD_PER_HOUR[strength]",
        decides="whether an hour in the gym raises or lowers TSB",
        moved_to="1.0",
        tests=(
            "tests/test_training_load.py",
            "tests/test_duration_load_calibration.py",
        ),
    ),
    PolicyGuard(
        target="services.training_load:FALLBACK_LOAD_PER_HOUR",
        decides="what an activity of an unknown sport is assumed to have cost",
        moved_to="1.0",
        tests=(
            "tests/test_training_load.py",
            "tests/test_duration_load_calibration.py",
        ),
    ),
    # --- Which activity was the planned session (#543, #589, #590) ----------
    PolicyGuard(
        target="services.ride_matching:ENDURANCE_SPLIT_SESSION_MAX_GAP_SECONDS",
        decides="how long a break can be and the long ride still be one ride",
        moved_to="28800",
        tests=("tests/test_split_session_window.py",),
    ),
    PolicyGuard(
        target="services.ride_matching:EASY_SPLIT_SESSION_MAX_GAP_SECONDS",
        decides="the same question for a recovery spin, where the answer differs",
        moved_to="600",
        tests=("tests/test_split_session_window.py",),
    ),
    PolicyGuard(
        target="services.ride_matching:COMBINED_DURATION_MIN_RATIO",
        decides="how far short of the plan two rides may fall and still be it",
        moved_to="0.01",
        tests=(
            "tests/test_split_session_window.py",
            "tests/test_matching_ignores_other_sports.py",
        ),
    ),
    # --- What the athlete is taken to be training for (#562, #566) ----------
    PolicyGuard(
        target="services.motivation_model:MAX_WEIGHT_NUDGE",
        decides="how fast behaviour may redefine what the athlete trains for",
        moved_to="1.0",
        tests=(
            "tests/test_motivation_weight_learning.py",
            "tests/test_motivation_model.py",
            "tests/test_motivation_inference.py",
        ),
    ),
    PolicyGuard(
        target="services.motivation_model:INITIAL_CONFIDENCE_CAP",
        decides="how much a single statement may establish on its first sighting",
        moved_to="1.0",
        tests=("tests/test_motivation_model.py", "tests/test_motivation_inference.py"),
    ),
    PolicyGuard(
        target="services.motivation_model:PROMOTION_MIN_OBSERVATIONS",
        decides="how much recurrence promotes a secondary objective to primary",
        moved_to="99",
        tests=("tests/test_motivation_model.py",),
    ),
    # --- Who the coach says the athlete is (#597) ---------------------------
    PolicyGuard(
        target="services.rider_identity:PATTERN_MIN_CONFIDENCE",
        decides=(
            "how much has to be observed before the coach describes someone's "
            "character back to them"
        ),
        moved_to="0.01",
        tests=("tests/test_identity_framed_recovery.py",),
    ),
    PolicyGuard(
        target="services.rider_identity:STYLE_MIN_CONFIDENCE",
        decides="when a performance attribute is a reading rather than a placeholder",
        moved_to="0.0",
        tests=("tests/test_identity_framed_recovery.py",),
    ),
    # --- What the coach may rely on believing (#387, #593) ------------------
    PolicyGuard(
        target="crud:ATHLETE_MEMORY_CONFIDENCE_STEP",
        decides="how many sightings turn an observation into something to act on",
        moved_to="0.01",
        tests=("tests/test_workout_curiosity.py", "tests/test_crud.py"),
    ),
    PolicyGuard(
        target="crud:ATHLETE_MEMORY_INITIAL_CONFIDENCE_CAP",
        decides="that one remark is never enough to describe the athlete",
        moved_to="1.0",
        tests=("tests/test_workout_curiosity.py", "tests/test_crud.py"),
    ),
)
