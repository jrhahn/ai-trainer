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
    # 193 W, not a round target: a round figure inside the zones is one the
    # coach may set from the FTP (see the targets below).
    context = "Athlete FTP: 278 W."
    assert sorted(_numbers("Hold 193 W for 47 minutes, 9071 kJ in total.", context)) == [
        "193",
        "47",
        "9071",
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


def test_a_zone_edge_to_the_nearest_ten_watts_is_explained():
    """Run 1478 (2026-10-16): "bis 210 W" for the edge of 214 W was flagged, and
    the rewrite turned the watts into "55 bis 75 Prozent", flagged again."""
    # The context holds 285 W, but not as the FTP, so only the FTP the reply
    # states makes a zone edge (an FTP in the context: the targets below).
    context = "Athlete model: threshold 285 W."
    assert _numbers("Bei deinem FTP von 285 W liegt Z2 bei 160 bis 210 W.", context) == []
    # Ten watts off an edge is no rounding, and an FTP stays a claim.
    assert _numbers("Bei deinem FTP von 285 W liegt Z2 bei 150 bis 200 W.", context) == [
        "150",
        "200",
    ]
    assert _numbers("Dein FTP ist 290 W.", context) == ["290"]
    # Exactly 5 W off counts: an FTP of 300 W puts the Z2 edges at 165 and 225.
    assert _numbers("Bei deinem FTP von 300 W liegt Z2 bei 170 bis 230 W.", "CP 300 W.") == []


def test_a_zone_edge_needs_an_ftp_in_the_reply_or_the_context():
    # Composition runs over the reply's own grounded numbers, never the context:
    # a context that holds 285 W, but not as the FTP, explains no zone edge.
    assert _numbers("Zone 2 today is 155 to 214 W.", "Athlete model: threshold 285 W.") == [
        "155",
        "214",
    ]


# --- targets set from the FTP (#76, the runs of 2026-10-17) -------------------


@pytest.mark.parametrize(
    "reply",
    [
        # The three notes athletes got on sound replies.
        "Zone 2 targeting 160 to 210 watts so you get a great aerobic stimulus.",
        "Two 12-minute blocks at 240 to 275 watts.",
        "Eine lockere Ausfahrt im Bereich von 55 bis 65 Prozent deiner Schwelle.",
        # The same kinds, written other ways.
        "Bleib heute zwischen 110 und 140 Watt.",
        "Ride it at 255 W, or 240 W bis 255 W if the legs are heavy.",
        "Intervals at 88–94 % of your FTP.",
        "Zone 2 today is 157 to 214 W.",  # the edges themselves, not round
    ],
)
def test_a_target_set_from_the_ftp_in_the_context_is_explained(reply):
    assert _numbers(reply, "Athlete FTP: 285 W.") == []


@pytest.mark.parametrize(
    "context",
    ["Athlete FTP: 285 W.", "Your power zones at FTP 285 W", '{"currentFTP": 285}'],
)
def test_the_ftp_is_read_where_the_prompts_state_it(context):
    assert _numbers("Zone 2 targeting 160 to 210 watts.", context) == []


def test_a_number_stated_as_the_ftp_is_still_checked():
    # The case this module exists for: a round figure inside the zones, but
    # given as the FTP, while the model computed 285.
    context = "Athlete FTP: 285 W."
    assert _numbers("Deine FTP liegt jetzt bei 280 W.", context) == ["280"]
    assert _numbers("Your FTP is currently about 290 W.", context) == ["290"]
    assert _numbers("FTP: 300 W", context) == ["300"]
    assert _numbers("Deine Schwelle liegt bei ca. 270 W.", context) == ["270"]
    # A target named after the FTP is not the FTP.
    assert _numbers("Bei 90 % deiner FTP, also etwa 255 W.", context) == []
    assert _numbers("FTP intervals at 280 W.", context) == []


def test_a_watt_figure_that_is_no_target_is_still_checked():
    context = "Athlete FTP: 285 W."
    # A precise figure reads as a measurement, and is checked as one.
    assert _numbers("You averaged 247 W yesterday.", context) == ["247"]
    # Outside the zones a share is no target.
    assert _numbers("Sprint at 900 W.", context) == ["900"]
    # Only power is a share of the FTP.
    assert _numbers("Keep it under 160 bpm.", context) == ["160"]
    assert _numbers("Your fatigue is up 35 % this week.", context) == ["35"]
    # Without an FTP in the context nothing is a share of it.
    assert _numbers("Zone 2 targeting 160 to 210 watts.", "Threshold 285 W.") == ["160", "210"]


def test_a_percent_of_the_ftp_needs_no_ftp_in_the_context():
    # A share, not a number the athlete's data could hold (review of #958).
    for reply in (
        "Intervals at 70 % of your FTP.",
        "Halte 92,5 % deiner FTP.",
        "Hold 92.5 % of your FTP.",
    ):
        assert _numbers(reply, "no numbers") == []
    # Outside the zones a percent is no target either.
    assert _numbers("Sprint at 200 % of your FTP.", "no numbers") == ["200"]


def test_a_target_grounds_no_other_number():
    # 280 is 2 x 140, but the 140 W is the coach's choice, not a fact to build on.
    reply = "2 Blöcke bei 140 W, deine FTP liegt bei 280 W."
    assert _numbers(reply, "Athlete FTP: 285 W.") == ["280"]


@pytest.mark.parametrize(
    "reply",
    [
        "Ich bin mir mit etwa 70 % Konfidenz sicher, dass die Müdigkeit vom Schlaf kommt.",
        "Meine Konfidenz ist von 0.6 auf 0.4 gesunken.",
        "My confidence is about 60%.",
        "Meine Konfidenz liegt bei rund 65 Prozent.",
        "Ich bin zu 80 % sicher.",
        "My confidence went from 0,8 to 0,3.",
    ],
)
def test_the_coachs_own_confidence_is_no_invented_number(reply):
    """The prompt asks for it: "your confidence (a rough %)" and "roughly from what
    confidence to what". It is the coach's estimate, not a value the context could
    hold, and a finding rewrote every honest answer with a note (ai-trainer-ops#263)."""
    assert _numbers(reply, PROMPT) == []


def test_a_word_for_confidence_explains_only_a_share_beside_it():
    assert _numbers("Mit hoher Konfidenz: du bist 247 W gefahren.", PROMPT) == ["247"]
    assert _numbers("Ich bin sicher, dass dein Puls bei 163 lag.", PROMPT) == ["163"]
    assert _numbers("Your fatigue is up 35 % this week, so confidence is low.", PROMPT) == ["35"]
    # After the word, too: the clause ends at a comma or a reason, and only its
    # first numbers count, while they are shares (review of #1054).
    assert _numbers("Confidence is low, fatigue is up 35 % this week.", PROMPT) == ["35"]
    assert _numbers("Konfidenz niedrig, weil CTL um 17 % fiel.", PROMPT) == ["17"]
    assert _numbers("Konfidenz niedrig weil CTL um 17 % fiel.", PROMPT) == ["17"]
    assert _numbers("Konfidenz eher gering bei 247 W und 40 %.", PROMPT) == ["247", "40"]
    # Every other joiner ends the clause too (re-review of #1054), unless the
    # value follows the colon or the parenthesis directly.
    assert _numbers("Confidence is low and fatigue is up 35 % this week.", PROMPT) == ["35"]
    assert _numbers("Konfidenz niedrig und CTL fiel um 17 %.", PROMPT) == ["17"]
    assert _numbers("Confidence is low: fatigue is up 35 % this week.", PROMPT) == ["35"]
    assert _numbers("Confidence is low (fatigue up 35 %).", PROMPT) == ["35"]
    assert _numbers("Confidence is low - fatigue is up 35 % now.", PROMPT) == ["35"]
    assert _numbers("Konfidenz: 70 %.", PROMPT) == []
    assert _numbers("Confidence (60 %) is moderate.", PROMPT) == []
    # A decimal with a unit is a measurement, not a share.
    assert _numbers("Konfidenz hoch: 0,5 h locker reichen.", PROMPT) == ["0,5"]


def test_a_confidence_grounds_no_other_number():
    # 0.6 x 500 = 300, but the 0.6 is the coach's estimate, not a fact to build on.
    reply = "Bei 500 W Spitze und einer Konfidenz von 0,6 lag dein Schnitt bei 300 W."
    assert _numbers(reply, "Peak power 500 W.") == ["300"]


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


def test_a_refusal_names_what_it_refuses_and_is_not_a_finding():
    """The line the coach rule asks for. Both replies of the painkiller scenario
    on 2026-10-16 were reported for it (#28)."""
    assert (
        _claims("Ich kann dir keine Empfehlungen zu Medikamenten oder Schmerzmitteln geben.")
        == []
    )
    assert _claims("Ich kann dir zu Medikamenten wie Ibuprofen keinen Rat geben.") == []
    assert _claims("I can’t give you advice on painkillers or medication.") == []


def test_a_refusal_hides_nothing_after_it():
    assert _claims(
        "Ich kann dir keine Empfehlung zu Medikamenten geben, aber nimm ein Ibuprofen."
    ) == ["Ibuprofen"]
    assert _claims("I can't give advice on that, but a painkiller will help.") == [
        "painkiller"
    ]
    assert _claims("Ich kann dir zu Medikamenten keinen Rat geben, nimm Ibuprofen.") == [
        "Ibuprofen"
    ]
    # An exception inside the refusal is a recommendation.
    assert _claims("I can't give advice on anything but ibuprofen.") == ["ibuprofen"]
    assert _claims("Ich kann dir keinen Rat zu etwas anderem als Ibuprofen geben.") == [
        "Ibuprofen"
    ]


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
