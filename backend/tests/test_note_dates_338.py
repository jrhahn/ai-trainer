"""The notes under a reply name days as the athlete says them
(review of 2026-11-01, UX F8, ai-trainer-ops#338).

A real run read "deshalb habe ich Donnerstag 2026-11-05, Donnerstag 2026-11-12
gekürzt", and a capped pair of Thursdays came out as "Donnerstag, Donnerstag".
"""

from __future__ import annotations

from datetime import date

from services import coach_chat
from services.coach_chat import SESSION_CAP


def _capped(*isos: str) -> list[dict]:
    return [
        {
            "date": iso,
            "weekday": "Thursday",
            "constraintType": SESSION_CAP,
            "capMinutes": 60,
        }
        for iso in isos
    ]


def test_two_capped_thursdays_are_told_apart():
    german = coach_chat._format_constraint_override_note(
        _capped("2026-11-05", "2026-11-12"), german=True
    )
    english = coach_chat._format_constraint_override_note(
        _capped("2026-11-05", "2026-11-12"), german=False
    )

    assert german.startswith("Donnerstag 5.11., Donnerstag 12.11. habe ich")
    assert english.startswith("I kept Thursday Nov 5, Thursday Nov 12 to 60 min")


def test_a_blocked_day_without_a_weekday_still_reads_as_a_day():
    blocked = [{"date": "2026-11-06", "weekday": None, "constraintType": "no_training"}]

    note = coach_chat._format_constraint_override_note(blocked, german=True)

    assert note.startswith("Für Freitag 6.11. konnte ich kein Training einplanen")
    assert "2026-11-06" not in note


def test_a_german_date_at_the_end_of_a_sentence_takes_no_second_period():
    note = coach_chat._illness_return_note(
        {("2026-11-05", 0): {}}, date(2026, 11, 1), german=True
    )

    assert note == "Diese Einheiten sind wieder in deinem Plan: Donnerstag 5.11."


def test_no_note_prints_an_iso_date_for_a_plan_day():
    for german in (True, False):
        assert "2026-11-05" not in coach_chat._ruled_out_note(["2026-11-05"], german)
        assert "2026-11-05" not in coach_chat._illness_return_note(
            {("2026-11-05", 0): {}}, date(2026, 11, 1), german
        )


def test_a_day_that_is_no_date_is_left_as_written():
    # Whatever the model put there is shown as it is rather than raising.
    assert coach_chat._dated_day("next week", german=True) == "next week"
    blocked = [{"date": "next week", "weekday": None, "constraintType": "no_training"}]
    assert "next week" in coach_chat._format_constraint_override_note(blocked, False)
