"""A maximum heart rate from age, for the prompt only (ai-trainer-ops#182).

An older athlete who walks with a heart-rate watch often knows no maximum. The
coach may then use one from age as a loose ceiling, never as zones: a formula
is off by ten beats or more either way. Analysis and the FTP evidence keep
reading the athlete's own maximum only.
"""

from datetime import datetime, timezone

import models
from schemas import UserProfileSchema
from services import heart_rate_caution
from services.prompts import profile_plan_rules


def _profile(**fields) -> dict:
    user = models.User(
        email="werner@example.com",
        hashed_password="x",
        follows_training_plan=False,
        **fields,
    )
    return UserProfileSchema.from_user(user).model_dump(by_alias=True)


def test_without_a_maximum_the_age_gives_a_labelled_estimate():
    age = 67
    born = datetime.now(timezone.utc).year - age

    estimate = _profile(birth_year=born)["estimatedMaxHeartRate"]

    assert estimate.startswith(f"about {round(208 - 0.7 * age)} bpm, from age")
    assert "off by 10 or more" in estimate


def test_the_athletes_own_maximum_wins():
    profile = _profile(birth_year=1958, max_heart_rate=172)

    assert profile["estimatedMaxHeartRate"] is None
    assert profile["maxHeartRate"] == 172


def test_without_an_age_there_is_no_estimate():
    assert _profile()["estimatedMaxHeartRate"] is None


def test_a_heart_rate_medication_drops_the_estimate_too():
    profile = _profile(birth_year=1958)

    shown = heart_rate_caution.prompt_profile(profile, heart_rate_targets_off=True)

    assert "estimatedMaxHeartRate" not in shown


def test_the_plan_rules_use_it_as_a_ceiling_only():
    assert "'estimatedMaxHeartRate' (ai-trainer-ops#182)" in profile_plan_rules(
        {"estimatedMaxHeartRate": "about 161 bpm"}
    )
    assert "only as a loose ceiling" in profile_plan_rules(
        {"estimatedMaxHeartRate": "about 161 bpm"}
    )
    assert "never set exact heart-rate zones" in profile_plan_rules(
        {"estimatedMaxHeartRate": "about 161 bpm"}
    )
