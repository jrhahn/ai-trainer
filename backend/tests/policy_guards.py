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
    # --- What the athlete's freshness is spent on (#602) ---------------------
    PolicyGuard(
        target="services.freshness_allocation:HEAT_PENALTY",
        decides="whether 34 °C changes what a day should be at all",
        moved_to="0.0",
        tests=("tests/test_freshness_allocation.py",),
    ),
    PolicyGuard(
        target="services.freshness_allocation:HEAT_TOLERANCE_SCALE[tolerant]",
        decides=(
            "whether an athlete who demonstrably rides fine in the heat still "
            "gets talked off their hot day"
        ),
        moved_to="1.0",
        tests=("tests/test_freshness_allocation.py",),
    ),
    PolicyGuard(
        target="services.freshness_allocation:FRESHNESS_PENALTY_SCALE",
        decides="whether a hard session is charged for the weekend it costs",
        moved_to="0.0",
        tests=("tests/test_freshness_allocation.py",),
    ),
    PolicyGuard(
        target="services.freshness_allocation:VALUE_GAIN",
        decides="how much of a weight vector it takes to call a demand important",
        moved_to="0.1",
        tests=("tests/test_freshness_allocation.py",),
    ),
    PolicyGuard(
        target="services.freshness_allocation:BAND_HIGH",
        decides="what the planner is told counts as high-value freshness",
        moved_to="0.99",
        tests=("tests/test_freshness_allocation.py",),
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
    # --- What the coach may claim the athlete's FTP is (#604) ---------------
    PolicyGuard(
        target="services.analysis:FTP_SUSTAINABLE_CEILINGS",
        decides=(
            "which FTP estimates the athlete's own rides rule out — the check "
            "that would have caught 130 % of threshold held for twelve minutes"
        ),
        # Moved wide enough that nothing is impossible any more.
        moved_to="((5.0, 9.9), (10.0, 9.9), (12.0, 9.9), (20.0, 9.9), (30.0, 9.9), (40.0, 9.9), (60.0, 9.9))",
        tests=("tests/test_analysis.py",),
    ),
    PolicyGuard(
        target="services.athlete_model_inference:_INTERVAL_ONLY_CONFIDENCE_CAP",
        decides="whether a hard interval set can pass for a threshold test",
        moved_to="0.99",
        tests=("tests/test_athlete_model_inference.py",),
    ),
    PolicyGuard(
        target="services.athlete_model_inference:_CORRECTED_CONFIDENCE_CAP",
        decides=(
            "how sure the coach may be about a number it had to correct against "
            "the athlete's own efforts"
        ),
        moved_to="0.99",
        tests=("tests/test_athlete_model_inference.py",),
    ),
    # --- Whether the coach is shown evidence at all (#528/#629) -------------
    PolicyGuard(
        target="services.rag:MIN_SIMILARITY",
        decides=(
            "whether a chunk is relevant enough to reach the coach as research "
            "— too low and 'move my Monday ride to Tuesday' returns five chunks "
            "under an evidence heading, too high and a real question silently "
            "returns nothing"
        ),
        # Back to the pre-#629 value. It looks harmless — it was the shipped
        # number for months — and after #640 shrank the corpus it sits inside
        # the science population, so real questions return nothing at all.
        moved_to="0.70",
        tests=("tests/test_rag.py",),
    ),

    # --- Whether the athlete's limiter can reach the corpus (#627) ----------
    PolicyGuard(
        target="services.knowledge_topics:MIN_TOPIC_OCCURRENCES",
        decides=(
            "whether one passing mention of FTP is enough to file a chunk under "
            "threshold — at 1 it tagged half the corpus, and a tag half the "
            "corpus carries cannot steer anything"
        ),
        moved_to="1",
        tests=("tests/test_knowledge_topics.py",),
    ),
    PolicyGuard(
        target="services.knowledge_topics:TOPIC_DOMINANCE_RATIO",
        decides=(
            "whether a topic mentioned in passing is tagged alongside the one "
            "the chunk is actually about"
        ),
        # 0.0 leaves only the occurrence floor, so every aside that clears it
        # becomes a topic the chunk gets promoted for.
        moved_to="0.0",
        tests=("tests/test_knowledge_topics.py",),
    ),
    PolicyGuard(
        target="services.rag:FOCUS_CANDIDATE_MULTIPLIER",
        decides=(
            "whether the athlete's diagnosed limiter can change which science "
            "the coach is shown, or only reorder what similarity already picked"
        ),
        # 1 is the value that looks harmless and silently reverts #627: the pool
        # is exactly the top-k similarity chose, so no ranking can add anything.
        moved_to="1",
        tests=("tests/test_rag.py",),
    ),
    # --- Whether lifting and riding are allowed to collide (#715) -----------
    PolicyGuard(
        target="services.interference:HEAVY_RIR_MAX",
        decides=(
            "how close to failure a prescribed lift has to be before the gate "
            "will move the plan for it — the boundary between 'the athlete lifted' "
            "and 'the athlete cannot ride tomorrow's intervals'"
        ),
        # Below 0 no prescription can ever be heavy, so the whole rule goes quiet
        # while every test about it still has a plan to run against.
        moved_to="-1",
        tests=("tests/test_interference_guards.py",),
    ),
    PolicyGuard(
        target="services.interference:HEAVY_PERCENT_E1RM_MIN",
        decides="the same boundary expressed as a fraction of the athlete's maximum",
        # Above any real prescription: 200 % of an e1RM is not a weight.
        moved_to="200.0",
        tests=("tests/test_interference_guards.py",),
    ),
    PolicyGuard(
        target="services.interference:MIN_SEPARATION_HOURS",
        decides=(
            "how far apart a ride and a gym session on one date have to be before "
            "they stop competing as adaptive signals"
        ),
        # 0 h clears every pair, however close together.
        moved_to="0.0",
        tests=("tests/test_interference_guards.py",),
    ),
    PolicyGuard(
        target="services.interference:KEY_WORKOUT_TYPES",
        decides=(
            "which sessions are the ones a week is built around, and therefore "
            "which ones heavy legs the day before are not allowed to cost"
        ),
        # An empty set leaves no session worth protecting.
        moved_to="frozenset()",
        tests=("tests/test_interference_guards.py",),
    ),
    PolicyGuard(
        target="services.interference:MODALITY_WEIGHT",
        decides=(
            "whether running interferes more than cycling — the modality finding "
            "that decides which of two competing findings resolves a session"
        ),
        # Equal weights make the two modalities indistinguishable.
        moved_to="{'running': 1.0, 'cycling': 1.0}",
        tests=("tests/test_interference_guards.py",),
    ),
    # --- How fast the athlete is, and what that costs them (#716) ----------
    PolicyGuard(
        target="services.run_model:THRESHOLD_FRACTION_OF_CRITICAL_SPEED",
        decides=(
            "the reference every rTSS figure and every pace zone is cut from — "
            "Critical Speed is a ~30 min pace, threshold pace is the hour"
        ),
        # Equal to CS: makes every rTSS figure ~8 % too small, and every zone
        # boundary too fast, while still looking entirely plausible.
        moved_to="1.0",
        tests=("tests/test_run_model.py", "tests/test_running_pace_model.py"),
    ),
    PolicyGuard(
        target="services.run_model:MIN_CS_SPAN_MINUTES",
        decides=(
            "how far apart two maximal efforts must sit before a Critical Speed "
            "fit over them means anything"
        ),
        # 0 min accepts three points crowded into a minute, where D′ is
        # invented by rounding error.
        moved_to="0.0",
        tests=("tests/test_run_model.py",),
    ),
    PolicyGuard(
        target="services.run_model:MAX_CS_SPEED_RESIDUAL",
        decides=(
            "whether the fitted hyperbola actually passes through the athlete's "
            "own efforts, which the usual r-squared cannot tell"
        ),
        # A residual bound of 100 % of speed accepts any curve at all.
        moved_to="1.0",
        tests=("tests/test_run_model.py",),
    ),
    PolicyGuard(
        target="services.run_model:MIN_CS_CURVE_DECLINE",
        decides=(
            "whether the pace–duration envelope descended, i.e. whether the short "
            "effort was maximal at all"
        ),
        # 1,0 accepts a flat curve, which puts CS above threshold.
        moved_to="1.0",
        tests=("tests/test_run_model.py",),
    ),
    PolicyGuard(
        target="services.run_model:MIN_GRADE_COST_FACTOR",
        decides=(
            "how much of a descent's cost grade adjustment is allowed to forgive, "
            "given that Minetti measured oxygen uptake and not eccentric load"
        ),
        # Raw Minetti reaches ~0,55; a floor at 0,1 prices a long descent as
        # very nearly free.
        moved_to="0.1",
        tests=("tests/test_run_model.py",),
    ),
    PolicyGuard(
        target="services.run_model:GAP_SEGMENT_METRES",
        decides=(
            "over what distance a gradient is computed, and therefore whether GPS "
            "altitude noise is smoothed or amplified"
        ),
        # Per-sample grade: one metre of wobble over three metres of running
        # reads as a 33 % climb.
        moved_to="0.0",
        tests=("tests/test_run_model.py",),
    ),
    PolicyGuard(
        target="services.run_model:MIN_CS_CONFIDENCE_FOR_LOAD",
        decides=(
            "how good the Critical Speed fit has to be before a threshold pace "
            "derived from it is allowed to price the athlete's runs"
        ),
        # 0,0 lets any fit at all set the athlete's whole load history.
        moved_to="0.0",
        tests=("tests/test_running_pace_model.py",),
    ),
    PolicyGuard(
        target="services.run_model:MAX_RUN_INTENSITY_FACTOR",
        decides=(
            "the point past which a run's pace is a GPS artefact rather than a "
            "very fast session"
        ),
        # 100× lets one stream artefact spike ATL.
        moved_to="100.0",
        tests=("tests/test_run_model.py",),
    ),
    # --- How much running the athlete's legs have met (#717) ---------------
    PolicyGuard(
        target="services.run_durability:MAX_ACUTE_CHRONIC_RATIO",
        decides=(
            "how far above their own four-week running exposure a single 7-day "
            "block may sit — the whole progression ceiling"
        ),
        # 100× is no ceiling at all: every running week passes, including the
        # six hours prescribed to someone who has never run.
        moved_to="100.0",
        tests=("tests/test_run_durability.py",),
    ),
    PolicyGuard(
        target="services.run_durability:MAX_WEEKLY_STEP_MINUTES",
        decides=(
            "whether the ceiling becomes more permissive the closer the athlete "
            "gets to the volume where overuse injuries happen"
        ),
        # 10 000 min removes the cap, so a 600 min/week runner is licensed three
        # further hours of impact in one week.
        moved_to="10000.0",
        tests=("tests/test_run_durability.py",),
    ),
    PolicyGuard(
        target="services.run_durability:MIN_LONG_RUN_STEP_MINUTES",
        decides="the smallest increase to a long run worth calling a progression",
        # 0 min hands an athlete whose longest run is 20 minutes a 26-minute
        # allowance, which is not a step anyone can execute.
        moved_to="0.0",
        tests=("tests/test_run_durability.py",),
    ),
    PolicyGuard(
        target="services.run_durability:MAX_LONG_RUN_STEP_MINUTES",
        decides=(
            "how much may be added to the week's single most concentrated "
            "exposure, which is not the same allowance as the week's total"
        ),
        # 10 000 min lets the long run grow by the full weekly step, so thirty
        # minutes spread over three days and thirty added to one become equal.
        moved_to="10000.0",
        tests=("tests/test_run_durability.py",),
    ),
    PolicyGuard(
        target="services.run_durability:BEGINNER_WEEKLY_MINUTES",
        decides=(
            "what a fit cyclist who has never run is allowed, which is the one "
            "figure #717 exists to put in front of the planner"
        ),
        # 10 000 min hands a CTL-90 cyclist an unlimited running week — the exact
        # failure the module was written to prevent.
        moved_to="10000.0",
        tests=("tests/test_run_durability.py",),
    ),
    PolicyGuard(
        target="services.run_durability:BEGINNER_LONGEST_RUN_MINUTES",
        decides="how long a single run may be for an athlete with no run history",
        # 10 000 min lets the whole beginner allowance go into one session.
        moved_to="10000.0",
        tests=("tests/test_run_durability.py",),
    ),
    PolicyGuard(
        target="services.run_durability:RUN_EXPOSURE_WINDOW_DAYS",
        decides=(
            "how far back running exposure is read, and therefore how fast a "
            "lapse lowers the ceiling"
        ),
        # A year makes a block of running from eleven months ago current
        # exposure, so a returning runner is treated as never having stopped.
        moved_to="365",
        tests=("tests/test_run_durability.py",),
    ),
    PolicyGuard(
        target="services.run_durability:ROLLING_WINDOW_DAYS",
        decides=(
            "the span a planned running load is measured over — rolling, so a "
            "block placed across a calendar week boundary is still one week"
        ),
        # 1 day measures each day alone, which is how a Sunday/Monday double
        # becomes two compliant half-weeks.
        moved_to="1",
        tests=("tests/test_run_durability.py",),
    ),
    PolicyGuard(
        target="services.run_durability:MAX_REPORTED_WINDOWS",
        decides="how many overlapping views of one build-up the coach is shown",
        # 100 states every window, so one block becomes a dozen near-identical
        # lines and the coach stops reading them.
        moved_to="100",
        tests=("tests/test_run_durability.py",),
    ),
    # --- Where a runner is limited, as opposed to a cyclist (#718) ----------
    PolicyGuard(
        target="services.limiter_detection:RUN_FRAC_CS_LIMITED",
        decides=(
            "how far Critical Speed must sit below the running aerobic ceiling "
            "before sustainable pace is named the limiter"
        ),
        # The cycling value. Critical Speed sits near 90 % of vVO₂max where FTP
        # sits near 75 % of MAP, so borrowing 0.72 means no runner is ever
        # pace-limited and every one of them is sent to the track.
        moved_to="0.72",
        tests=("tests/test_per_sport_limiter_chain.py",),
    ),
    PolicyGuard(
        target="services.limiter_detection:RUN_FRAC_SPEED_CEILING",
        decides=(
            "when a runner's sustainable pace is close enough to their top-end "
            "speed that the ceiling is what has to move"
        ),
        # The cycling value again, and the dangerous direction: 0.80 calls an
        # ordinary runner ceiling-limited and prescribes the highest-impact
        # sessions in the sport.
        moved_to="0.80",
        tests=("tests/test_per_sport_limiter_chain.py",),
    ),
    PolicyGuard(
        target="services.athlete_model_inference:_VVO2MAX_DURATION_MIN",
        decides=(
            "which envelope duration stands in for the velocity at VO₂max, and "
            "therefore the denominator of every running limiter decision"
        ),
        # 1 min is an anaerobic sprint, not an aerobic ceiling: it inflates the
        # denominator and makes every runner look pace-limited.
        moved_to="1.0",
        tests=("tests/test_per_sport_limiter_chain.py",),
    ),
    PolicyGuard(
        target="services.athlete_model_inference:_DURABILITY_RUN_S",
        decides=(
            "how long a run must be before its half-split pace counts as "
            "durability evidence"
        ),
        # 0 s makes a 20-minute recovery jog durability evidence, and a jog that
        # finishes easier than it started reads as a fade.
        moved_to="0",
        tests=("tests/test_per_sport_limiter_chain.py",),
    ),
    # --- How evenly the week was loaded (#747) ------------------------------
    PolicyGuard(
        target="services.training_monotony:MONOTONY_FLAG",
        decides=(
            "how uneven a week has to be before the coach is told anything at "
            "all — the only threshold in the module that is quoted as literature"
        ),
        # 100 is unreachable: a week of seven identical days computes to the cap
        # of 5, so the audit goes permanently silent and the signal that no other
        # number in the app carries is lost again.
        moved_to="100.0",
        tests=("tests/test_training_monotony.py",),
    ),
    PolicyGuard(
        target="services.training_monotony:MIN_DAILY_MEAN_LOAD",
        decides=(
            "the load below which a flat week is a routine rather than a risk — "
            "the half of the rule carried in this app's own currency"
        ),
        # 0 flags every monotonous week regardless of size, so an athlete doing
        # twenty minutes a day is told their training is relentless.
        moved_to="0.0",
        tests=("tests/test_training_monotony.py",),
    ),
    PolicyGuard(
        target="services.training_monotony:MONOTONY_CAP",
        decides=(
            "where the quotient stops being a measurement, which is also what a "
            "zero-deviation week is reported as"
        ),
        # 1000 removes the cap in practice and lets a one-point difference across
        # a flat week reach the coach as "monotony 170".
        moved_to="1000.0",
        tests=("tests/test_training_monotony.py",),
    ),
    PolicyGuard(
        target="services.training_monotony:STRAIN_BASELINE_TOLERANCE",
        decides=(
            "how close to their own baseline a week's strain may sit and still be "
            "described as unchanged"
        ),
        # 0 means an athlete repeating one week exactly is told their strain is
        # below their own baseline, on the strength of floating-point error.
        moved_to="0.0",
        tests=("tests/test_training_monotony.py",),
    ),
    PolicyGuard(
        target="services.training_monotony:BASELINE_WINDOW_DAYS",
        decides=(
            "how much history the athlete's own strain baseline is read from, "
            "which is the only thing strain is ever compared against"
        ),
        # 7 leaves no complete earlier window, so strain loses its comparison and
        # the one figure that is not quotable as literature goes unqualified.
        moved_to="7",
        tests=("tests/test_training_monotony.py",),
    ),
    PolicyGuard(
        target="services.training_monotony:ROLLING_WINDOW_DAYS",
        decides="the window the whole distribution audit is measured over",
        # 1 day has no deviation to speak of, so every athlete is maximally
        # monotonous and the audit degenerates into "did you train yesterday".
        moved_to="1",
        tests=("tests/test_training_monotony.py",),
    ),
)
