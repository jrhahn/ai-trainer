"""The seam between the deterministic model and the language layer (ai-trainer-ops#28).

``services/output_audit`` answers two questions about a coach reply without
asking an LLM anything: did it state a number nobody gave it, and did it say
something that reads as medicine. Both are heuristics, so what matters as much
as the finds is the list of things that must *not* be reported — a check that
cries wolf on every reply gets switched off, and then it protects nothing.

The cases below are therefore roughly half negatives, and most of them are
things the coach says all the time: a date, a rep count, a rounded FTP, a
referral to a doctor, "treat yourself to a rest day".
"""

from __future__ import annotations

import logging

import pytest

from services import metrics
from services.output_audit import (
    CHECK_CLAIMS,
    CHECK_NUMBERS,
    SMALL_INTEGER_MAX,
    audit,
    context_values,
    forbidden_claims,
    report,
    unexplained_numbers,
)


def _numbers(text: str, context) -> list[str]:
    return [finding.detail for finding in unexplained_numbers(text, context)]


def _claims(text: str) -> list[str]:
    return [finding.detail for finding in forbidden_claims(text)]


# ---------------------------------------------------------------------------
# Number provenance: what the context accounts for
# ---------------------------------------------------------------------------

# A fragment of the shape ask_trainer actually sends: the deterministic model's
# numbers, rendered for the coach to read.
PROMPT = (
    "Athlete FTP: 278 W. Last 7 days TSS: 412. CTL 61.4, ATL 58.0, TSB 3.4.\n"
    "Planned Tuesday: intervals, 75 minutes, target power 305 W.\n"
    "Weather Wednesday: -2 degrees, wind 18 km/h.\n"
)


def test_a_number_from_the_prompt_is_not_a_finding():
    assert _numbers("Your FTP is 278 W, so ride Tuesday at 305 W.", PROMPT) == []


def test_an_invented_ftp_is_reported():
    # The motivating case: the model computed 278 and the coach said 285.
    assert _numbers("Your FTP is 285 W.", PROMPT) == ["285"]


def test_rounding_a_context_value_is_not_invention():
    # CTL 61.4 reported as 61, ATL 58.0 as 58.
    assert _numbers("Your fitness sits at 61 and fatigue at 58.", PROMPT) == []


def test_a_sign_the_prose_carries_is_not_a_different_number():
    # The context holds -2 degrees; the reply spells the sign out in words.
    assert _numbers("Wednesday is 2 degrees below freezing.", PROMPT) == []


def test_a_total_composed_from_numbers_in_the_reply_is_accepted():
    # "3×20 min" is 60 minutes of work and the context holds no 60 anywhere, so
    # a literal reading would report the total the coach correctly added up.
    context = "Planned Tuesday: intervals, 3 sets of 20 minutes."
    assert _numbers("Do 3×20 min, so 60 minutes of work.", context) == []


def test_composition_cannot_launder_an_invented_number():
    """A composed total is only accepted over numbers that are real first.

    Otherwise the rule is a hole rather than an allowance: any invented number
    could be explained by two more invented ones, and the check would be quiet
    exactly when the coach was making things up most freely.
    """
    context = "Athlete FTP: 278 W."
    assert sorted(_numbers("Hold 190 W for 47 minutes, 8930 kJ in total.", context)) == [
        "190",
        "47",
        "8930",
    ]


def test_small_integers_are_enumeration_rather_than_measurement():
    assert _numbers(f"Do {SMALL_INTEGER_MAX} hard efforts over 4 days.", "no numbers") == []


def test_a_small_integer_above_the_allowlist_is_not_exempt():
    assert _numbers(f"Do {SMALL_INTEGER_MAX + 1} hard efforts.", "no numbers") == [
        str(SMALL_INTEGER_MAX + 1)
    ]


def test_a_decimal_inside_the_allowlist_range_is_a_measurement():
    """Nobody writes 9.4 repetitions.

    The allowlist exempts enumeration, and a fractional part is what separates a
    count from a reading — which matters, because the small end of the range is
    exactly where blood values and TSB live.
    """
    assert _numbers("Your haemoglobin is 9.4.", "no numbers") == ["9.4"]


def test_a_date_is_not_three_numbers():
    assert _numbers("Your race on 2026-08-23 is the target.", "no numbers") == []


def test_a_clock_time_is_not_two_numbers():
    assert _numbers("Start at 07:45 to beat the heat.", "no numbers") == []


def test_a_digit_inside_a_name_is_not_a_measurement():
    assert _numbers("Keep it in Z2 and your VO2max will look after itself.", "x") == []


def test_a_german_decimal_comma_resolves_against_an_english_context():
    # The coach answers in the athlete's language; the prompt is English.
    assert _numbers("Dein CTL liegt bei 61,4.", PROMPT) == []


def test_a_thousands_separator_resolves_either_way():
    assert _numbers("That ride was 1,250 kJ.", "Work done: 1250 kJ") == []
    assert _numbers("That ride was 1.250 kJ.", "Work done: 1250 kJ") == []


def test_context_may_be_a_nested_structure_rather_than_a_prompt():
    context = {"athleteModel": {"ftp": 278, "zones": [{"upper": 305}]}}
    assert _numbers("Ride at 305 W; your FTP is 278 W.", context) == []


def test_context_values_ignores_booleans():
    # ``True`` is an ``int`` in Python, and a 1 the model never stated would
    # otherwise explain a 1 the coach invented.
    assert context_values({"completed": True, "flag": False}) == []


def test_no_context_means_the_provenance_check_does_not_run():
    """An empty context would make every number a finding.

    That is a statement about the caller, not about the reply, so ``audit``
    declines to make it.
    """
    findings = audit("Your FTP is 285 W.", None)
    assert [finding.check for finding in findings] == []


def test_prose_without_digits_is_never_a_number_finding():
    assert _numbers("Ride easy, enjoy the sunshine, and listen to your legs.", "") == []


# ---------------------------------------------------------------------------
# Forbidden claims: the line this product must not cross
# ---------------------------------------------------------------------------


def test_asserting_a_named_condition_is_reported():
    assert _claims("Given the fatigue pattern, you have anemia.") == ["anemia"]


def test_a_hedged_assertion_is_still_an_assertion():
    assert _claims("You might have an iron deficiency here.") == ["iron deficiency"]


def test_signs_of_a_condition_is_an_assertion():
    assert _claims("This is signs of overtraining syndrome.")


def test_a_conditional_is_not_an_assertion():
    """The sentence the product wants, and the one a word list reports."""
    assert _claims("If you have asthma, your doctor is the person to ask.") == []
    assert _claims("Unless you have a heart condition, this is fine.") == []


def test_a_condition_named_without_any_claim_is_not_reported():
    assert _claims("Iron deficiency is common in endurance athletes.") == []


def test_naming_a_medical_act_is_reported_whoever_the_subject_is():
    assert _claims("I'd start a treatment for that.") == ["treatment"]
    assert _claims("The usual therapy is rest.") == ["therapy"]
    assert _claims("Your prognosis is good.") == ["prognosis"]


def test_a_disclaimer_is_not_a_finding():
    assert _claims("This is not medical advice.") == []
    assert _claims("I am not a doctor, so please see a physician about that.") == []
    assert _claims("Seek medical advice before you ride again.") == []


def test_a_referral_does_not_hide_a_claim_in_the_same_sentence():
    """Safe phrases are blanked, not whole sentences skipped.

    Otherwise appending "see a doctor" would be a way to say anything at all.
    """
    assert _claims("You have anemia, so see a doctor.") == ["anemia"]


def test_the_coaching_senses_of_medical_words_are_not_findings():
    assert _claims("Treat yourself to a rest day.") == []
    assert _claims("A healthy dose of zone 2 will fix this.") == []


def test_detection_does_not_depend_on_capitalisation():
    assert _claims("YOU HAVE ANEMIA.") == ["ANEMIA"]
    assert _claims("A TREATMENT is needed.") == ["TREATMENT"]


def test_an_ordinary_reply_is_clean():
    assert (
        _claims(
            "Nice work on Sunday. Tuesday's intervals look right for where your "
            "fitness is, and I'd keep Wednesday easy so the legs come back."
        )
        == []
    )


# ---------------------------------------------------------------------------
# report(): what production is allowed to learn
# ---------------------------------------------------------------------------

SENTINEL = "your haemoglobin is 9.4 and you have anemia"


def _counter(name: str, **labels) -> float:
    value = metrics.REGISTRY.get_sample_value(name, labels)
    return 0.0 if value is None else value


def test_report_counts_the_audit_and_its_findings():
    before_audits = _counter("coach_output_audits_total", surface="unit-test")
    before_claims = _counter(
        "coach_output_audit_findings_total", surface="unit-test", check=CHECK_CLAIMS
    )

    findings = report(SENTINEL, "nothing in here", surface="unit-test")

    assert any(finding.check == CHECK_CLAIMS for finding in findings)
    assert any(finding.check == CHECK_NUMBERS for finding in findings)
    assert _counter("coach_output_audits_total", surface="unit-test") == before_audits + 1
    assert (
        _counter(
            "coach_output_audit_findings_total", surface="unit-test", check=CHECK_CLAIMS
        )
        > before_claims
    )


def test_a_clean_reply_still_counts_as_audited():
    """Without the denominator, the findings counter cannot be read.

    A rise in findings would be indistinguishable from a rise in questions.
    """
    before = _counter("coach_output_audits_total", surface="unit-test-clean")
    assert report("Ride easy today.", "Ride easy today.", surface="unit-test-clean") == []
    assert (
        _counter("coach_output_audits_total", surface="unit-test-clean") == before + 1
    )


def test_report_puts_neither_the_reply_nor_its_numbers_in_the_log(caplog):
    """A coach reply is the athlete's health data (#499).

    So is every number in it, and so is which condition it named — the fact
    that *this* athlete was told about anemia is information about them. The log
    line carries counts and nothing else.
    """
    with caplog.at_level(logging.DEBUG, logger="services.output_audit"):
        report(SENTINEL, "nothing in here", surface="unit-test-log")

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert logged, "the audit logged nothing at all, so the assertion proves nothing"
    assert SENTINEL not in logged
    assert "anemia" not in logged
    assert "haemoglobin" not in logged
    assert "9.4" not in logged
    assert CHECK_CLAIMS in logged


def test_report_never_raises(monkeypatch):
    """Observability that can break the athlete's answer is worse than none."""

    def explode(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("services.output_audit.audit", explode)
    assert report("anything", "anything", surface="unit-test-raise") == []


@pytest.mark.parametrize(
    "text",
    ["", "   ", "---", "€", "\n\n", "0", "...", "1e9", "NaN"],
)
def test_the_checkers_are_total_over_odd_input(text):
    audit(text, PROMPT)
    audit(PROMPT, text)
