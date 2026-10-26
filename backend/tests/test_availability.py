"""Unit tests for services/availability.py."""

from __future__ import annotations

from datetime import date

from services.availability import (
    extract_availability_constraints,
    extract_constraint_lift,
)

TODAY = date(2026, 6, 3)  # a Wednesday


def test_empty_text_returns_no_constraints():
    assert extract_availability_constraints("", today=TODAY) == []
    assert extract_availability_constraints("   ", today=TODAY) == []


def test_requires_both_unavailable_and_training_context():
    # training context but no "unavailable" phrasing
    assert extract_availability_constraints("I love training", today=TODAY) == []
    # unavailable phrasing but no training context
    assert extract_availability_constraints("I am unavailable", today=TODAY) == []


def test_weekday_constraint_extracted():
    result = extract_availability_constraints(
        "I am unavailable for training on friday", today=TODAY
    )
    assert len(result) == 1
    assert result[0]["constraint_type"] == "no_training"
    assert result[0]["weekday"] == "friday"
    assert result[0]["constraint_date"] == result[0]["expires_on"]


def test_tomorrow_and_today_constraints():
    tomorrow = extract_availability_constraints(
        "unavailable for training tomorrow", today=TODAY
    )
    assert tomorrow[0]["constraint_date"] == "2026-06-04"

    today = extract_availability_constraints(
        "unavailable for training today", today=TODAY
    )
    assert today[0]["constraint_date"] == "2026-06-03"


def test_constraints_are_deduplicated_per_date():
    result = extract_availability_constraints(
        "unavailable for training monday and today", today=TODAY
    )
    dates = [r["constraint_date"] for r in result]
    assert len(dates) == len(set(dates))


def test_long_session_required_constraint_extracted():
    result = extract_availability_constraints(
        "lass uns eine lange einheit am samstag machen", today=TODAY
    )
    required = [r for r in result if r["constraint_type"] == "required_workout"]
    assert len(required) == 1
    assert required[0]["weekday"] == "saturday"
    assert required[0]["required_workout"]["workoutType"] == "endurance"
    assert required[0]["required_workout"]["minDurationMinutes"] == 120


def test_long_session_duration_is_parsed():
    result = extract_availability_constraints(
        "long ride on saturday, 3 hours", today=TODAY
    )
    required = [r for r in result if r["constraint_type"] == "required_workout"]
    assert required and required[0]["required_workout"]["minDurationMinutes"] == 180


def test_plain_training_mention_creates_no_required_constraint():
    # "training" alone is not a long-session phrase.
    result = extract_availability_constraints(
        "I love training on saturday", today=TODAY
    )
    assert all(r["constraint_type"] != "required_workout" for r in result)


def test_german_phrasing_supported():
    result = extract_availability_constraints(
        "Ich habe keine Zeit für training am freitag", today=TODAY
    )
    assert any(r["weekday"] == "friday" for r in result)


def test_rest_day_not_pinned_as_required_session():
    # #412: "tomorrow off? saturday and sunday i could do long endurance" must
    # not cross-apply the long-session requirement onto the rest day. The prod
    # bug pinned a required endurance session onto the exact day the athlete
    # asked to take off, so the coach could never clear it.
    result = extract_availability_constraints(
        "and tomorrow off? saturday and sunday i could do long endurance",
        today=TODAY,  # Wednesday -> tomorrow = Thursday 2026-06-04
    )
    required = [r for r in result if r["constraint_type"] == "required_workout"]
    required_dates = {r["constraint_date"] for r in required}
    # The off day (tomorrow) is not required.
    assert "2026-06-04" not in required_dates
    # Saturday and Sunday still get the long endurance requirement.
    assert required_dates == {"2026-06-06", "2026-06-07"}
    assert all(r["required_workout"]["workoutType"] == "endurance" for r in required)


def test_intent_does_not_leak_across_clauses():
    # A "no training" clause and a separate "long ride" clause each bind only to
    # their own day, even when both appear in one message.
    result = extract_availability_constraints(
        "cannot ride monday. long ride on saturday", today=TODAY
    )
    by_date = {(r["constraint_type"], r["constraint_date"]) for r in result}
    assert ("no_training", "2026-06-08") in by_date  # next monday
    assert ("required_workout", "2026-06-06") in by_date  # saturday
    # Monday is not also required, and saturday is not also a no-training day.
    assert ("required_workout", "2026-06-08") not in by_date
    assert ("no_training", "2026-06-06") not in by_date


def test_same_intent_still_spans_days_joined_by_and():
    # "and" joins same-intent days within one clause; both must be captured.
    result = extract_availability_constraints(
        "unavailable for training monday and today", today=TODAY
    )
    dates = {r["constraint_date"] for r in result}
    assert dates == {"2026-06-08", "2026-06-03"}  # next monday, today


# --- Lifting constraints (#437) -------------------------------------------


def test_lift_bare_request_detected_without_dates():
    # "pls lift that constraint" is the exact phrasing the override note invites.
    result = extract_constraint_lift("pls lift that constraint", today=TODAY)
    assert result == {"lift": True, "dates": []}


def test_lift_german_sperre_aufheben_detected():
    result = extract_constraint_lift("hebe die Sperre bitte auf", today=TODAY)
    assert result["lift"] is True
    assert result["dates"] == []


def test_lift_with_named_day_resolves_date():
    result = extract_constraint_lift("remove the constraint on friday", today=TODAY)
    assert result["lift"] is True
    assert result["dates"] == ["2026-06-05"]  # next friday


def test_lift_ignores_day_in_unrelated_clause():
    # The day named in a separate clause is not swept into the lift.
    result = extract_constraint_lift(
        "cancel that constraint. monday i am unavailable", today=TODAY
    )
    assert result["lift"] is True
    assert result["dates"] == []


def test_no_lift_for_ordinary_message():
    assert extract_constraint_lift("make friday a rest day", today=TODAY) == {
        "lift": False,
        "dates": [],
    }
    # "remove the warmup" is not a constraint lift.
    assert extract_constraint_lift("remove the warmup", today=TODAY)["lift"] is False


# ---------------------------------------------------------------------------
# Sentences from the owner's coach chat, paraphrased (ai-trainer-ops#72)
# ---------------------------------------------------------------------------


def _kinds(text: str) -> list[tuple[str, str]]:
    return [
        (item["constraint_type"], item["constraint_date"])
        for item in extract_availability_constraints(text, today=TODAY)
    ]


def test_each_day_in_one_sentence_gets_its_own_intent():
    # Wednesday: Thursday gym, Friday off, Saturday the long ride. All three
    # used to be pinned as required long sessions.
    assert _kinds(
        "morgen upperbody und core im gym und freitag off, samstag wie geplant die lange einheit"
    ) == [("no_training", "2026-06-05"), ("required_workout", "2026-06-06")]


def test_days_joined_by_and_share_the_intent_after_them():
    assert _kinds("saturday and sunday i could do a long ride") == [
        ("required_workout", "2026-06-06"),
        ("required_workout", "2026-06-07"),
    ]


def test_a_stated_day_off_is_captured_without_a_training_word():
    assert _kinds("donnerstag muss frei bleiben, da hat jemand geburtstag") == [
        ("no_training", "2026-06-04")
    ]
    assert _kinds("Keep in mind tomorrow is rest day") == [
        ("no_training", "2026-06-04")
    ]
    assert _kinds("friday i dont have time for training") == [
        ("no_training", "2026-06-05")
    ]


def test_a_question_pins_nothing():
    assert _kinds("tomorrow off? would that work") == []
    assert _kinds("why did the plan drop sunday's long endurance ride?") == []
    assert (
        _kinds("macht es sinn morgen gym und freitag off, samstag die lange einheit?")
        == []
    )


def test_an_instrument_that_was_off_is_not_a_day_off():
    assert _kinds("my power meter was off today") == []


def test_a_stated_absence_blocks_every_day_of_it():
    days = _kinds(
        "ab dem 4. November bis zum 26. November bin ich im Urlaub, da kann ich nicht trainieren"
    )

    assert days[0] == ("no_training", "2026-11-04")
    assert days[-1] == ("no_training", "2026-11-26")
    assert len(days) == 23


def test_a_range_without_an_absence_or_too_long_a_range_is_ignored():
    assert _kinds("from 4 to 26 november the trails are wet") == []
    assert _kinds("from 1 january to 30 april i am on holiday") == []


def test_a_free_day_in_german_is_an_offer_not_a_block():
    assert _kinds("Samstag habe ich frei, da kann ich lang fahren") == []
    assert _kinds("Sonntag ist frei") == []


def test_date_range_edges():
    # Started already: only today onward is blocked.
    started = extract_availability_constraints(
        "vom 1. bis 5. Juni bin ich im Urlaub", today=TODAY
    )
    assert [c["constraint_date"] for c in started] == [
        "2026-06-03",
        "2026-06-04",
        "2026-06-05",
    ]
    # Over the turn of the year.
    winter = _kinds("from 28 december to 2 january i am on holiday")
    assert winter[0] == ("no_training", "2026-12-28")
    assert winter[-1] == ("no_training", "2027-01-02")
    # A range already over this year means next year's.
    spring = _kinds("from 4 to 6 march i am on holiday")
    assert [d for _t, d in spring] == ["2027-03-04", "2027-03-05", "2027-03-06"]
    # A date that does not exist is ignored, also once moved to next year.
    assert _kinds("from 30 to 31 february i am on holiday") == []
    leap = extract_availability_constraints(
        "from 29 february to 5 march i am on holiday", today=date(2028, 6, 1)
    )
    assert leap == []
    # A question about a range blocks nothing; a statement followed by a
    # question still does, and the holiday word may come first.
    assert _kinds("can i train from 4 to 26 november? i'm on holiday") == []
    assert (
        len(
            _kinds(
                "Ab dem 4. bis zum 26. November bin ich im Urlaub. Kannst du den Plan anpassen?"
            )
        )
        == 23
    )
    assert (
        len(_kinds("I'm on holiday from 4 to 26 November. Can you adjust the plan?"))
        == 23
    )


# ---------------------------------------------------------------------------
# What the athlete rules out, did already, or only limits (ai-trainer-ops#110)
# ---------------------------------------------------------------------------

import pytest  # noqa: E402  (kept beside the cases it parametrises)

SAT = "2026-06-06"
SUN = "2026-06-07"
FRI = "2026-06-05"
TOMORROW = "2026-06-04"


@pytest.mark.parametrize(
    "text",
    [
        # Ruled out, not asked for. Each pinned a two-hour ride before.
        "Keine lange Ausfahrt am Samstag, ich bin auf einer Hochzeit.",
        "No long ride on Saturday, I'm at a wedding.",
        "Samstag keine lange Runde, nur locker",
        # Done already, or a complaint about it.
        "Am Sonntag war die lange Ausfahrt super",
        "The long ride on Sunday was too much for me",
        "Die lange Runde am Samstag hat gut getan",
        "Ich bin am Sonntag eine lange Runde gefahren",
        "I did a long ride on Sunday",
        # Off work is time to ride, not a rest day.
        "I'm off on Friday so I can ride all day",
        "I'm off work on Friday so I can ride more",
        # Something else cannot happen, and the athlete has time to ride.
        "Montag kann ich nicht ins Büro, also habe ich Zeit zum Fahren!",
        # A limit on how long, not a day off.
        "Sonntag kann ich nicht so lange fahren, nur 1 Stunde",
        # A holiday with the bike.
        "Vom 4. bis 26. November bin ich im Urlaub auf Mallorca, mit Rad!",
        # Not away, back again, or away with the bike.
        "Am Samstag bin ich nicht weg.",
        "Ab Samstag bin ich wieder da.",
        "Am Samstag bin ich mit Rad weg.",
        "Samstag bin ich mit dem Rad unterwegs.",
        "I'm not away on Saturday.",
    ],
)
def test_what_is_ruled_out_done_or_only_limited_pins_nothing(text):
    assert _kinds(text) == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # A day the athlete cannot do, with nothing else said about training.
        ("Samstag kann ich nicht, bin auf einer Hochzeit", [("no_training", SAT)]),
        ("Sonntag kann ich nicht, bin weg", [("no_training", SUN)]),
        (
            "Am Sonntag fahre ich nicht, ich bin auf einer Hochzeit",
            [("no_training", SUN)],
        ),
        (
            "Wir feiern Geburtstag, deshalb kann ich morgen nicht",
            [("no_training", TOMORROW)],
        ),
        ("Saturday I can't, I'm at a wedding", [("no_training", SAT)]),
        # Away, with no "can't" and no training word (the gap #63 noted).
        ("Am Sonntag bin ich weg", [("no_training", SUN)]),
        ("Ich bin am Samstag weg", [("no_training", SAT)]),
        ("Samstag bin ich nicht da", [("no_training", SAT)]),
        ("Sunday I'm away", [("no_training", SUN)]),
        ("I'm away on Saturday", [("no_training", SAT)]),
        ("Saturday I'm not around", [("no_training", SAT)]),
        ("I won't be around on Sunday", [("no_training", SUN)]),
        ("Am Samstag bin ich nicht zu Hause", [("no_training", SAT)]),
        ("Sunday I'm not home", [("no_training", SUN)]),
        ("Saturday I'm out of town", [("no_training", SAT)]),
        ("I'll be away on Sunday", [("no_training", SUN)]),
        # Off work, and asking for the long ride that time makes possible.
        (
            "Tomorrow I'm off work, so a long ride would be great.",
            [("required_workout", TOMORROW)],
        ),
        (
            "Thursday is my day off, so I have time for a long ride",
            [("required_workout", TOMORROW)],
        ),
        # Asked for, in the forms the request still takes.
        ("Bitte plane am Samstag eine lange Ausfahrt", [("required_workout", SAT)]),
        (
            "Samstag ist eine lange Ausfahrt mit Freunden geplant",
            [("required_workout", SAT)],
        ),
        ("Am Samstag gegen 9 Uhr eine lange Ausfahrt", [("required_workout", SAT)]),
        # A day that is off still is.
        ("Friday is off", [("no_training", FRI)]),
        ("Friday off, no training", [("no_training", FRI)]),
    ],
)
def test_what_is_still_captured(text, expected):
    assert _kinds(text) == expected


def test_next_weekday_said_on_that_weekday_is_a_week_away():
    """ "Nächsten Sonntag", said on a Sunday, was stored as that same day."""
    sunday = date(2026, 10, 18)

    def dates(text: str) -> list[str]:
        return [
            c["constraint_date"]
            for c in extract_availability_constraints(text, today=sunday)
        ]

    assert dates("Nächsten Sonntag kann ich nicht.") == ["2026-10-25"]
    assert dates("Next Sunday I'm away.") == ["2026-10-25"]
    assert dates("Am kommenden Sonntag bin ich verreist.") == ["2026-10-25"]
    # The day named on its own is still today, and "next" Saturday the coming one.
    assert dates("Sonntag kann ich nicht.") == ["2026-10-18"]
    assert dates("Nächsten Samstag kann ich nicht.") == ["2026-10-24"]


def test_an_absence_said_as_away_blocks_every_day_of_it():
    assert _kinds("Vom 8. bis 10. Juni bin ich weg.") == [
        ("no_training", "2026-06-08"),
        ("no_training", "2026-06-09"),
        ("no_training", "2026-06-10"),
    ]
    # Away with the bike is time to ride (review of #961).
    assert _kinds("Vom 8. bis 10. Juni bin ich mit Rad weg.") == []


def test_a_start_time_is_not_a_duration():
    """'Start 8h' made an eight-hour ride; a duration after it still counts."""
    (start_only,) = extract_availability_constraints(
        "Samstag lange Ausfahrt, Start 8h", today=TODAY
    )
    assert start_only["required_workout"]["minDurationMinutes"] == 120
    (with_hours,) = extract_availability_constraints(
        "Samstag lange Ausfahrt, Start 8h, 4 Stunden", today=TODAY
    )
    assert with_hours["required_workout"]["minDurationMinutes"] == 240


def test_a_holiday_without_the_bike_still_blocks():
    assert len(_kinds("Vom 4. bis 26. November bin ich im Urlaub auf Mallorca")) == 23


@pytest.mark.parametrize(
    "text",
    [
        "Samstag haben wir die gesamten Jungs dabei, lange Ausfahrt",
        "Am Samstag bin ich gegen neun bereit für die lange Ausfahrt",
        "Ich bin wieder gesund, Samstag lange Ausfahrt",
    ],
)
def test_words_that_only_look_like_a_participle_keep_the_request(text):
    assert _kinds(text) == [("required_workout", SAT)]


@pytest.mark.parametrize(
    "text",
    [
        # A complaint about a coming ride, in the present tense.
        "Die lange Ausfahrt am Samstag ist mir zu viel",
        # A day off work, said on its own, is no rest day.
        "Friday is my day off",
        "I have the day off on Friday",
        "I'm off on Friday",
        # A limit on how long, where the training verb is the one negated.
        "Sonntag kann ich nicht lange fahren",
        "Sunday I can't ride long, only 1 hour",
        # "kann nicht" about something else, beside a training word.
        "Mittwoch kann ich nicht zur Arbeit, Training geht trotzdem",
    ],
)
def test_each_new_rule_on_its_own(text):
    assert _kinds(text) == []


def test_time_on_hand_is_no_block():
    """'No time pressure' is unavailability phrasing; having time overrides it."""
    assert _kinds(
        "On Saturday there's no time pressure, I have time for a long ride"
    ) == [("required_workout", SAT)]


# --- Review of #923 -----------------------------------------------------------


def test_kann_nicht_with_the_day_between_still_blocks():
    assert _kinds("Ich kann nicht am Samstag trainieren") == [("no_training", SAT)]
    # Driving somewhere is not training.
    assert _kinds("Am Montag kann ich nicht zur Arbeit fahren") == []


def test_a_past_or_a_what_in_another_part_keeps_the_request():
    assert _kinds("Ich war lange nicht draußen, Sonntag lange Ausfahrt") == [
        ("required_workout", SUN)
    ]
    assert _kinds("Sonntag lange Ausfahrt, was meinst du dazu") == [
        ("required_workout", SUN)
    ]


def test_each_day_keeps_its_own_availability():
    assert _kinds("Donnerstag kann ich nicht trainieren, Freitag kann ich fahren") == [
        ("no_training", TOMORROW)
    ]


def test_a_word_ending_in_nt_is_no_negation():
    """Only a real contraction negates: 'student' is no "n't"."""
    from services.availability import _has_positive_availability

    assert _has_positive_availability("saturday my student can ride along")
    assert not _has_positive_availability("saturday i can't ride, i don't have time")


def test_a_typographic_apostrophe_is_an_apostrophe():
    """Phone keyboards write ’ (review of #923)."""
    assert _kinds("Saturday I can\u2019t, I\u2019m at a wedding") == [
        ("no_training", SAT)
    ]
    assert _kinds("friday i don\u2019t have time for training") == [
        ("no_training", FRI)
    ]
    assert _kinds("I\u2019m off on Friday") == []


def test_a_long_ride_ruled_out_for_one_day_is_not_asked_for_by_the_next_part():
    """The part after a day's mention can belong to the next day."""
    kinds = _kinds("Keine lange Ausfahrt am Samstag, lange Ausfahrt am Sonntag")

    assert ("required_workout", SAT) not in kinds
    # Sunday is not captured either: its request sits in Saturday's segment.
    # A segmenter that captures it would change this on purpose, not silently.
    assert kinds == []
