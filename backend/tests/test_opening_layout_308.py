"""The opening message's layout and dates (ai-trainer-ops#308).

The coach's first message ran its parts together and wrote the race date as the
facts carried it, 2027-04-26. The prompt now asks for a blank line between the
parts and the assumptions as a list, and the facts carry the date as the
athlete's language writes it.
"""

from __future__ import annotations

import pytest

from services import opening_message, prompts


@pytest.mark.parametrize(
    "value, language, said",
    [
        ("2027-04-26", "German", "26. April 2027"),
        ("2027-05-02", "German", "2. Mai 2027"),
        ("2027-03-09", "German", "9. März 2027"),
        ("2027-04-26", "English", "26 April 2027"),
        ("2027-12-31", None, "31 December 2027"),
    ],
)
def test_a_date_is_written_the_way_the_language_writes_it(value, language, said):
    assert opening_message.spoken_date(value, language) == said


@pytest.mark.parametrize(
    "value, language",
    [("2027-04-26", "French"), ("next spring", "German"), ("", "English")],
)
def test_a_date_stays_as_it_is_in_another_language_or_when_it_is_none(value, language):
    assert opening_message.spoken_date(value, language) == value


@pytest.mark.parametrize(
    "language, said", [("German", "12. Mai 2027"), ("English", "12 May 2027")]
)
def test_the_model_gets_the_race_date_in_the_athletes_words(language, said):
    answers = {"goal": "race", "raceDate": "2027-05-12", "race": "Gran Fondo"}

    shown = opening_message.shown_answers(answers, language)

    assert shown["raceDate"] == said
    assert answers["raceDate"] == "2027-05-12"


@pytest.mark.parametrize("language", ["German", "English", "French"])
def test_the_prompt_asks_for_paragraphs_and_the_assumptions_as_a_list(language):
    system = prompts.opening_message_system(language, "cycling")

    assert "a blank line between the four parts" in system
    assert "as a bullet list, one item each" in system
    assert "after a blank line" in system
    assert "never as YYYY-MM-DD" in system
