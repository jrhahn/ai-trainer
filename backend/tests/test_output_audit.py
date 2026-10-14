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


# --- what the first scenario run reported wrongly (#28, #76) -----------------


def test_a_zone_edge_from_the_ftp_the_reply_states_is_one_step_away():
    # Zone 2 from FTP 285 is 157-214 W; "about 155" is that edge, rounded.
    reply = "With your FTP of 285 W, easy zone 2 today sits at about 155 to 214 W."
    assert _numbers(reply, "Athlete FTP: 285 W.") == []


def test_a_zone_edge_without_the_ftp_beside_it_is_still_reported():
    # Composition runs over the reply's own grounded numbers, never the context.
    assert _numbers("Zone 2 today is 155 to 214 W.", "Athlete FTP: 285 W.") == ["155", "214"]


def test_only_a_power_licenses_a_zone_edge():
    # 157 is the zone 2 edge of 285, but this 285 is a heart-rate-like count.
    context = "Athlete FTP: 300 W. Climb: 285 m."
    assert _numbers("The climb is 285 m, ride it at 157 W.", context) == ["157"]
    assert _numbers("Your FTP is 300 W, so ride it at 165 W.", context) == []


def test_minutes_followed_by_another_unit_are_not_a_duration():
    assert _numbers("Ride 3 h 30 km of it on gravel.", "Planned today: 210 minutes.") == ["30"]


def test_a_round_figure_may_stand_for_what_rounds_to_it():
    context = "Zone 2: 157-214 W."
    assert _numbers("Keep it above 155 W.", context) == []
    assert _numbers("Keep it above 153 W.", context) == ["153"]  # not a round figure


@pytest.mark.parametrize(
    ("written", "context", "explained"),
    [
        ("100", "Value: 97.5", True),  # the floor itself is a round figure
        ("105", "Value: 107.5", True),
        ("105", "Value: 107.6", False),  # just beyond half a step
        ("95", "Value: 97", False),  # below the floor the step does not apply
        ("102", "Value: 100", False),  # not a multiple of five
    ],
)
def test_the_round_figure_allowance_starts_at_its_floor(written, context, explained):
    assert (_numbers(f"Hold {written} W.", context) == []) is explained


def test_the_invented_form_value_stays_a_finding():
    # #76, fresh-after-rest: no load data in the context, yet a form value.
    context = "Athlete FTP: 285 W. Rest days: 2. Planned today: sweet spot, 75 minutes."
    reply = "With an FTP of 285 W and your form deep in the negative around -37, wait."
    assert _numbers(reply, context) == ["37"]


@pytest.mark.parametrize(
    "duration",
    ["3 Stunden und 30 Minuten", "3 hours and 30 minutes", "3h30", "3 h 30 min", "3 Std. 30 Min"],
)
@pytest.mark.parametrize("context", ["Planned today: 210 minutes.", "Planned today: 3.5 h."])
def test_hours_and_minutes_compose_a_stated_duration(duration, context):
    assert _numbers(f"Today is a long base ride of {duration} in zone 2.", context) == []


def test_a_duration_the_context_does_not_hold_is_still_reported():
    reply = "Heute stehen 4 Stunden und 45 Minuten an."
    assert _numbers(reply, "Planned today: 180 minutes.") == ["45"]


def test_hours_then_a_distance_is_not_a_duration():
    assert _numbers("Ride 3 h, 30 km of it climbing.", "Planned today: 210 minutes.") == ["30"]


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


def test_symptoms_named_to_send_the_athlete_on_are_not_a_finding():
    # #76, ill-with-fever, and its English twin.
    assert _claims(
        "Wenn die Symptome anhalten oder schlimmer werden, lass das bitte von einem "
        "Arzt abklären."
    ) == []
    assert _claims("If symptoms persist, see a doctor.") == []


def test_symptoms_read_as_a_finding_are_still_reported():
    assert _claims("Your symptoms point to overreaching.") == ["symptoms"]


def test_the_coaching_senses_of_medical_words_are_not_findings():
    assert _claims("Treat yourself to a rest day.") == []
    assert _claims("A healthy dose of zone 2 will fix this.") == []


def test_a_german_assertion_is_reported():
    """The coach answers in the athlete's language (ai-trainer-ops#78)."""
    assert _claims("Das klingt nach einer Sehnenentzündung.") == ["Sehnenentzündung"]
    assert _claims("Du hast vermutlich Eisenmangel.") == ["Eisenmangel"]
    assert _claims("Die übliche Therapie ist Ruhe.") == ["Therapie"]


def test_a_german_conditional_or_disclaimer_is_not_a_finding():
    assert _claims("Wenn du Asthma hast, frag deinen Arzt.") == []
    assert _claims("Falls du eine Herzerkrankung hast, kläre das ärztlich ab.") == []
    assert (
        _claims("Ich bin kein Arzt und das ist keine Diagnose, lass das ärztlich abklären.")
        == []
    )


def test_a_conditional_is_a_whole_word():
    """"Job" and "relief" end in a conditional; neither is one."""
    from services.output_audit import _CONDITIONAL

    assert _CONDITIONAL.search("ob ") and _CONDITIONAL.search("so if ")
    assert not _CONDITIONAL.search("ein Job ") and not _CONDITIONAL.search("relief ")


def test_the_separable_referral_needs_no_blanking_and_hides_nothing():
    """"Kläre das ärztlich ab" holds no medical word, so a claim next to it stays."""
    assert _claims("Kläre das bitte ärztlich ab.") == []
    assert _claims("Du hast vermutlich Eisenmangel, kläre das ärztlich ab.") == [
        "Eisenmangel"
    ]


def test_the_german_coaching_senses_are_not_findings():
    """A race-time forecast and letting a cold heal are coaching, not medicine."""
    assert _claims("Die Prognose für deine Zielzeit ist gut.") == []
    assert _claims("Lass die Erkältung ausheilen, dann geht es weiter.") == []
    assert _claims("Eisenmangel ist bei Ausdauersportlern häufig.") == []


def test_naming_a_drug_is_a_medical_act_in_either_language():
    assert _claims("Nimm vor der Ausfahrt ein Ibuprofen.") == ["Ibuprofen"]
    assert _claims("A painkiller will get you through it.") == ["painkiller"]
    assert _claims("Ein Espresso mit Koffein vor dem Rennen hilft.") == []
    assert _claims("Take an aspirin first.") == ["aspirin"]
    assert _claims("Aspiring racers train smart.") == []
    # Word-start stems: plurals and compounds count.
    assert _claims("Antibiotika und Schmerzmitteln helfen hier nicht.") == [
        "Antibiotika",
        "Schmerzmitteln",
    ]


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
