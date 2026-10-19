"""A session that names a power zone gets that zone's watts (ai-trainer-ops#75)."""

from datetime import date, timedelta

import pytest

from services.zone_label import align_target_to_named_zone, ftp_figures, with_profile_ftp

FTP = 320  # Z2 = 176–240 W at this FTP


def _day(description: str, low: int, high: int, **extra) -> dict:
    return {
        "date": "2026-10-11",
        "title": "Easy ride",
        "description": description,
        "targetPower": {"low": low, "high": high},
        **extra,
    }


def test_the_session_from_the_owners_chat_gets_its_z2_watts():
    # Described as Z2, targeted at recovery watts.
    day = _day("Steady Z2 endurance, keep it smooth.", 110, 145)

    aligned = align_target_to_named_zone(day, FTP)

    assert aligned["targetPower"] == {"low": 176, "high": 240}
    assert aligned["description"] == day["description"]


@pytest.mark.parametrize(
    "day",
    [
        _day("Steady Zone 2 endurance.", 190, 230),  # agrees
        _day("Steady Z2 endurance.", 160, 200),  # overlaps, close enough
        _day("Recovery spin, stay below Z2.", 110, 145),  # Z2 as a ceiling
        _day("Bleib unter Z2, ganz locker.", 110, 145),
        _day("Z2 with two blocks of Z4.", 110, 145),  # two zones named
        _day("Recovery spin in Z1.", 200, 230),  # Z1 is open below
        _day("Easy endurance.", 110, 145),  # no zone named
        _day("Max Z2 today.", 110, 145),
        _day("Höchstens Z2.", 110, 145),
        _day("Not Z2, keep it lighter.", 110, 145),
        _day("Bis Z2 ist erlaubt.", 110, 145),
        _day("Up to Z2.", 110, 145),
        _day("Steady Z2.", 110, 145, intervals=[{"durationMinutes": 5}]),
    ],
)
def test_anything_else_is_left_alone(day):
    assert align_target_to_named_zone(day, FTP) is day


def test_a_target_above_the_zone_comes_down_too():
    aligned = align_target_to_named_zone(_day("Steady Z2.", 260, 300), FTP)

    assert aligned["targetPower"] == {"low": 176, "high": 240}


def test_the_title_alone_can_name_the_zone():
    day = {
        "title": "Z3 tempo",
        "description": None,
        "targetPower": {"low": 110, "high": 145},
    }

    assert align_target_to_named_zone(day, FTP)["targetPower"] == {
        "low": 240,
        "high": 288,
    }


def test_without_an_ftp_or_a_target_nothing_moves():
    day = _day("Steady Z2.", 110, 145)

    assert align_target_to_named_zone(day, None) is day
    assert align_target_to_named_zone(day, -50) is day
    for broken in ({"low": 110}, {"low": "110", "high": 145}, {"high": None}):
        partial = {**day, "targetPower": broken}
        assert align_target_to_named_zone(partial, FTP) is partial
    assert align_target_to_named_zone({"description": "Steady Z2."}, FTP) == {
        "description": "Steady Z2."
    }


@pytest.mark.asyncio
async def test_the_plan_gate_aligns_a_written_day(client, auth_headers):
    from sqlalchemy import select

    import crud
    import models
    from services import plan_pipeline
    from tests.conftest import TestSessionLocal

    async with TestSessionLocal() as db:
        user = await db.scalar(select(models.User))
        user.current_ftp = FTP
        day = {
            **_day(
                "Steady Z2 endurance.",
                110,
                145,
                workoutType="endurance",
                durationMinutes=75,
            ),
            "date": (date.today() + timedelta(days=2)).isoformat(),
        }
        result = await plan_pipeline.commit_plan(
            db, user, [day], base_plan=[], source="coach_chat"
        )
        await db.commit()
        stored = (await crud.get_training_plan(db, user.id)).plan

    assert result.plan[0]["targetPower"]["low"] == 176
    assert stored[0]["targetPower"] == {"low": 176, "high": 240}


# --- The FTP the words name is the profile's (ai-trainer-ops#147) ---------------


def test_an_ftp_figure_in_the_words_becomes_the_profiles():
    # The owner's plan named 320 W for months beside targets from another FTP.
    day = _day(
        "Z2 endurance, 175–215 W (56–72 % deiner FTP von 320 W).",
        175,
        215,
        keyFocusPoints=["Basierend auf FTP 320 W", "Kadenz um 90"],
        workoutPurpose="Grundlage, gerechnet mit einer 320 W FTP",
    )

    fixed = with_profile_ftp(day, 300)

    assert fixed["description"] == "Z2 endurance, 175–215 W (56–72 % deiner FTP von 300 W)."
    assert fixed["keyFocusPoints"] == ["Basierend auf FTP 300 W", "Kadenz um 90"]
    assert fixed["workoutPurpose"] == "Grundlage, gerechnet mit einer 300 W FTP"
    assert fixed["targetPower"] == day["targetPower"]
    assert fixed["title"] == day["title"]


@pytest.mark.parametrize(
    "text",
    [
        "Steady Z2 at 60 % deiner FTP.",  # a share, no figure
        "Intervals at 225 W (75 % FTP).",  # watts beside the share they are
        "Deine FTP von 300 W bleibt, wie sie ist.",  # the profile's already
        "Deine FTP von 280 W auf 300 W angehoben.",  # a change, not the FTP in use
    ],
)
def test_words_that_agree_or_name_a_change_are_left_as_written(text):
    day = _day(text, 175, 215)

    assert with_profile_ftp(day, 300) is day


def test_shares_beside_the_figure_stay_as_written():
    # A known limit, from the review of the PR: the shares were worked out
    # from the old figure. At 300 W, 175–215 W is 58–72 %.
    day = _day("175–215 W (56–72 % deiner FTP von 320 W).", 175, 215)

    assert with_profile_ftp(day, 300)["description"] == "175–215 W (56–72 % deiner FTP von 300 W)."


def test_without_a_profile_ftp_nothing_changes():
    day = _day("Built on an FTP of 320 W.", 175, 215)

    assert with_profile_ftp(day, None) is day
    assert with_profile_ftp(day, 0) is day


def test_the_ftp_figure_is_the_last_of_a_chain():
    assert ftp_figures("FTP von 250 W auf 300 W") == {300}
    assert ftp_figures("Built on a 320 W FTP.") == {320}
