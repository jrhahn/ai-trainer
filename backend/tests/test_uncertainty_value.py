"""One gate deciding whether an uncertainty is worth resolving (#582).

Four modules used to make this call independently, and none of them asked the
question that matters: is reducing this worth what reducing it costs? The
reasoning existed in exactly one place — as English inside the inquiry prompt —
so it covered one channel of four and could not be tested or recorded.
"""

from __future__ import annotations

import inspect

import pytest

from services import (
    athlete_inquiry,
    experiment_suggestion,
    hypothesis_generation,
    open_question_generation,
    uncertainty_value,
)
from services.motivation_model import DEFAULT_WEIGHTS
from services.uncertainty_value import (
    CHANNEL_COST,
    CHANNEL_DATA_DISCOUNT,
    CHANNEL_EXPERIMENT,
    CHANNEL_HYPOTHESIS,
    CHANNEL_INQUIRY,
    CHANNEL_OPEN_QUESTION,
    NEUTRAL_RELEVANCE,
    VALUE_RULES,
    evaluate,
)

FTP_QUESTION = "Is FTP underestimated? A 30-minute threshold test would settle it."
WHY_QUESTION = "You cut Sunday's long ride short twice — what happened?"


# --- The rule this gate was lifted from ------------------------------------


def test_the_athlete_is_not_asked_what_the_data_will_answer():
    """The inquiry prompt's STEP 1, made executable: FTP, fatigue and pacing are
    all settled by the next few weeks of riding, and asking about them burns the
    athlete's patience on something the coach should infer."""
    decision = evaluate(channel=CHANNEL_INQUIRY, text=FTP_QUESTION)
    assert not decision.should_raise
    assert decision.reducibility > 0.5
    assert "riding will show anyway" in decision.reason


def test_the_same_uncertainty_is_perfectly_fine_as_an_open_question():
    """"If more riding would settle it, it is an open question or an experiment,
    not a question for the athlete." Being answerable by data is *why* an open
    question exists — penalising it there would refuse to write down the very
    things the record is for."""
    decision = evaluate(channel=CHANNEL_OPEN_QUESTION, text=FTP_QUESTION)
    assert decision.should_raise
    assert decision.reducibility == 0.0


def test_what_only_the_athlete_knows_clears_the_highest_bar():
    decision = evaluate(channel=CHANNEL_INQUIRY, text=WHY_QUESTION)
    assert decision.should_raise
    assert "lives_in_the_athlete" in {f.rule.name for f in decision.firings}


def test_recognising_a_narrow_topic_does_not_turn_a_raise_into_a_decline():
    """A niggle question moves only ``health``, which is a small share of the
    cold-start vector. Without the exclusive-knowledge bonus, naming the topic
    would drop it below the bar that an unreadable sentence clears — punishing
    the rules for working.

    The bonus does not force it above the neutral prior, and should not: an
    athlete who has told us health barely matters to them has said something, and
    the weights are there to be listened to. What must not happen is a *recognised*
    thing-only-they-know falling out of the channel that exists to ask it.
    """
    recognised = evaluate(
        channel=CHANNEL_INQUIRY, text="Is that knee niggle still there?"
    )
    assert recognised.should_raise
    assert "lives_in_the_athlete" in {f.rule.name for f in recognised.firings}


def test_an_athlete_who_weights_health_at_nothing_is_not_asked_about_niggles():
    """The other side of the same coin, and the reason the bonus is additive
    rather than a floor."""
    decision = evaluate(
        channel=CHANNEL_INQUIRY,
        text="Is that knee niggle still there?",
        weights={**DEFAULT_WEIGHTS, "health": 0.0},
    )
    assert not decision.should_raise


@pytest.mark.parametrize(
    ("text", "trap"),
    [
        ("Will they hold threshold power to the end?", "will"),
        ("Does the interval workout suit their week?", "workout"),
        ("Do hill repeats build sustainable power?", "hill"),
    ],
)
def test_a_pattern_does_not_fire_on_a_word_that_merely_contains_it(text, trap):
    """Word boundaries are not decoration. A bare "ill" matches "will" and a bare
    "work" matches "workout", which would file half the training vocabulary as
    a health risk or as something only the athlete knows."""
    firings = {f.rule.name for f in evaluate(channel=CHANNEL_HYPOTHESIS, text=text).firings}
    assert "moves_risk" not in firings, trap
    assert "lives_in_the_athlete" not in firings, trap


# --- Cost is per channel, not global ---------------------------------------


def test_the_same_score_clears_one_channel_and_not_another():
    """The acceptance criterion, and the reason a single gate earns its keep."""
    text = "Does your fatigue response to back-to-back threshold blocks differ?"
    as_hypothesis = evaluate(channel=CHANNEL_HYPOTHESIS, text=text)
    as_inquiry = evaluate(channel=CHANNEL_INQUIRY, text=text)

    assert as_hypothesis.should_raise
    assert not as_inquiry.should_raise
    assert as_hypothesis.threshold < as_inquiry.threshold


def test_asking_the_athlete_is_the_most_expensive_thing_the_coach_can_do():
    """Attention is the only resource here that does not replenish."""
    assert CHANNEL_COST[CHANNEL_INQUIRY] > CHANNEL_COST[CHANNEL_EXPERIMENT]
    assert CHANNEL_COST[CHANNEL_EXPERIMENT] > CHANNEL_COST[CHANNEL_OPEN_QUESTION]
    assert CHANNEL_COST[CHANNEL_OPEN_QUESTION] > CHANNEL_COST[CHANNEL_HYPOTHESIS]


def test_only_the_channels_that_spend_effort_pay_the_data_discount():
    assert CHANNEL_DATA_DISCOUNT[CHANNEL_INQUIRY] == 1.0
    assert CHANNEL_DATA_DISCOUNT[CHANNEL_EXPERIMENT] > 0
    assert CHANNEL_DATA_DISCOUNT[CHANNEL_OPEN_QUESTION] == 0.0
    assert CHANNEL_DATA_DISCOUNT[CHANNEL_HYPOTHESIS] == 0.0


def test_an_experiment_for_something_plain_riding_settles_is_not_worth_a_slot():
    decision = evaluate(
        channel=CHANNEL_EXPERIMENT,
        text="Does CTL keep rising? Keep training and watch the fitness trend.",
    )
    assert not decision.should_raise


# --- Relevance is this athlete's, not a global notion ----------------------


def test_the_athletes_own_weights_decide_what_is_worth_resolving():
    """`utility = Σ wᵢ · scoreᵢ` from #566 applies directly: an uncertainty is
    worth resolving in proportion to what it moves *for this athlete*."""
    race_text = "Will they peak in time for the target race?"
    racer = evaluate(
        channel=CHANNEL_HYPOTHESIS,
        text=race_text,
        weights={**DEFAULT_WEIGHTS, "race_performance": 0.6},
    )
    non_racer = evaluate(
        channel=CHANNEL_HYPOTHESIS,
        text=race_text,
        weights={**DEFAULT_WEIGHTS, "race_performance": 0.01},
    )
    assert racer.relevance > non_racer.relevance


def test_an_uncertainty_that_moves_nothing_this_athlete_cares_about_is_declined():
    decision = evaluate(
        channel=CHANNEL_HYPOTHESIS,
        text="Will they peak in time for the target race?",
        weights={"race_performance": 0.0, "adaptation": 1.0},
    )
    assert not decision.should_raise


def test_a_brand_new_athlete_is_not_gated_by_a_model_nobody_built():
    """Cold start: no motivation model yet must not mean no uncertainties."""
    decision = evaluate(channel=CHANNEL_HYPOTHESIS, text=WHY_QUESTION, weights=None)
    assert decision.should_raise


def test_a_partial_or_broken_weight_vector_falls_back_per_component():
    decision = evaluate(
        channel=CHANNEL_HYPOTHESIS,
        text="Does the plan volume suit them?",
        weights={"adaptation": "not a number", "enjoyment": 0.4},
    )
    assert decision.should_raise
    assert 0.0 <= decision.relevance <= 1.0


# --- Silence is not a reason to decline ------------------------------------


def test_text_no_rule_recognises_is_not_thereby_vetoed():
    """Otherwise the gate quietly becomes "only topics we wrote patterns for"."""
    decision = evaluate(channel=CHANNEL_INQUIRY, text="Zwei Sätze auf Deutsch.")
    assert decision.relevance == NEUTRAL_RELEVANCE
    assert decision.should_raise


def test_the_neutral_prior_clears_every_channel():
    for channel, cost in CHANNEL_COST.items():
        assert NEUTRAL_RELEVANCE >= cost, channel


def test_a_decision_that_lands_exactly_on_the_bar_is_a_raise():
    """0.15 + 0.3 is not 0.45 in binary floating point, and a question must not
    be lost to a rounding error seventeen digits down."""
    decision = evaluate(
        channel=CHANNEL_HYPOTHESIS,
        text="unrecognised",
        threshold=NEUTRAL_RELEVANCE,
    )
    assert decision.should_raise


# --- The decision is recorded ----------------------------------------------


def test_every_decision_carries_the_rules_that_fired_and_why():
    """In the shape of the weight-event audit trail (#566): by name, not
    reconstructed by a reader."""
    decision = evaluate(channel=CHANNEL_INQUIRY, text=FTP_QUESTION)
    record = decision.as_record()

    assert record["channel"] == CHANNEL_INQUIRY
    assert record["raised"] is False
    assert record["threshold"] == pytest.approx(CHANNEL_COST[CHANNEL_INQUIRY])
    names = {rule["rule"] for rule in record["rules"]}
    assert "fitness_trend" in names
    for rule in record["rules"]:
        # A person reading this six months later gets the argument, not a slug.
        assert rule["signal"]
        assert rule["aspect"] in ("reducibility", "relevance")


def test_the_reason_reads_as_a_sentence():
    raised = evaluate(channel=CHANNEL_HYPOTHESIS, text=WHY_QUESTION)
    assert raised.reason.startswith("Worth it:")
    declined = evaluate(channel=CHANNEL_INQUIRY, text=FTP_QUESTION)
    assert declined.reason.startswith("Not worth it:")


@pytest.mark.parametrize("rule", list(VALUE_RULES))
def test_every_rule_is_written_down_in_words(rule):
    """The whole rule has to be reviewable as a whole rather than discovered one
    branch at a time."""
    assert rule.name and rule.signal and rule.patterns


# --- No channel may bypass the gate ----------------------------------------


@pytest.mark.parametrize(
    "module",
    [
        hypothesis_generation,
        open_question_generation,
        experiment_suggestion,
        athlete_inquiry,
    ],
)
def test_every_generator_passes_through_the_one_gate(module):
    """The acceptance criterion, as a guardrail: a channel that raises an
    uncertainty without asking what it is worth is a test failure, not a code
    review comment."""
    source = inspect.getsource(module)
    assert "uncertainty_value.evaluate(" in source
    assert "record_value_decision(" in source


def test_the_gate_knows_a_cost_for_every_channel_it_names():
    channels = {
        value
        for name, value in vars(uncertainty_value).items()
        if name.startswith("CHANNEL_") and isinstance(value, str)
    }
    assert channels == set(CHANNEL_COST)
    assert channels == set(CHANNEL_DATA_DISCOUNT)
