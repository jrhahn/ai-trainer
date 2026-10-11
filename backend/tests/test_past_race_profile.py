"""A race already run is history in every prompt, not next year's target (ai-trainer-ops#71)."""

from datetime import date

from services import prompts

TODAY = date(2026, 10, 11)
# The owner's profile after the season, as the account export of 2026-10-11 shows it.
PAST = {
    "trainingGoal": "race",
    "raceDate": "2026-05-14",
    "raceDescription": "80km 4000hm",
    "currentFTP": 285,
}


def test_a_past_race_moves_to_last_race():
    shown = prompts.prompt_profile(PAST, TODAY)

    assert "raceDate" not in shown and "raceDescription" not in shown
    assert shown["trainingGoal"] == "no upcoming race in the profile"
    assert shown["lastRace"] == {"date": "2026-05-14", "description": "80km 4000hm"}
    assert shown["currentFTP"] == 285


def test_an_upcoming_race_and_race_day_itself_are_left_alone():
    upcoming = {**PAST, "raceDate": "2027-05-13"}
    race_day = {**PAST, "raceDate": TODAY.isoformat()}

    assert prompts.prompt_profile(upcoming, TODAY) is upcoming
    assert prompts.prompt_profile(race_day, TODAY) is race_day
    assert "taper timing" in prompts.race_profile_context_section(upcoming, TODAY)


def test_the_race_section_calls_a_past_race_past():
    section = prompts.race_profile_context_section(PAST, TODAY)

    assert "Past race: 2026-05-14" in section
    assert "There is no upcoming race" in section
    assert "taper timing" not in section


def test_a_garbled_race_date_is_passed_through_untouched():
    garbled = {**PAST, "raceDate": "next spring"}

    assert prompts.prompt_profile(garbled, TODAY) is garbled


def test_the_plan_prompt_no_longer_offers_the_past_race_as_a_target():
    prompt = prompts.generate_plan_user(PAST, TODAY.isoformat(), "")

    assert "Past race: 2026-05-14" in prompt  # still known, as history
    assert "taper timing" not in prompt
    assert '"raceDate"' not in prompt


def test_a_goal_other_than_race_is_kept():
    general = {**PAST, "trainingGoal": "general_fitness"}

    assert prompts.prompt_profile(general, TODAY)["trainingGoal"] == "general_fitness"


def test_every_prompt_that_dumps_the_profile_goes_through_prompt_profile():
    # A new prompt that dumps the raw profile would bring the phantom race back.
    import inspect

    source = inspect.getsource(prompts)
    assert "marked_json(profile)" not in source
    assert source.count("marked_json(prompt_profile(profile") >= 7
