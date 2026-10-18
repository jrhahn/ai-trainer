"""The athlete's email and surname must not reach the model (ops#125).

prompt_profile was built from a schema carrying email and full name, and the
race-event-feedback prompt dumped the raw profile. Both went to the provider,
on testers' own keys too. The fix drops the email and keeps the first name
(needed for the greeting); these tests fail if an email or the surname appears
in an assembled prompt.
"""

import services.prompts as prompts

_PROFILE = {
    "name": "John Doe",
    "email": "john.doe@example.com",
    "bikeType": "road",
    "trainingGoal": "race",
    "raceDate": "2027-05-01",
    "raceDescription": "80 km gravel",
}


def test_strip_profile_pii_drops_email_and_keeps_first_name():
    out = prompts.strip_profile_pii(_PROFILE)
    assert "email" not in out
    assert out["name"] == "John"


def test_prompt_profile_carries_no_email_or_surname():
    out = prompts.prompt_profile(dict(_PROFILE))
    assert "email" not in out
    assert out.get("name") == "John"


def test_prompt_profile_strips_pii_on_the_past_race_path_too():
    out = prompts.prompt_profile(
        {"name": "Jane Roe", "email": "j.roe@x.co", "trainingGoal": "race", "raceDate": "2020-01-01"}
    )
    assert "email" not in out
    assert out.get("name") == "Jane"
    assert out.get("trainingGoal") == "no upcoming race in the profile"  # past-race logic intact


def test_assembled_plan_prompt_has_no_email_or_surname():
    text = prompts.generate_plan_user(dict(_PROFILE), "2026-10-18", "")
    assert "john.doe@example.com" not in text
    assert "@" not in text  # no address of any shape survived
    assert "Doe" not in text  # surname gone
    assert "John" in text  # first name still there for the greeting
