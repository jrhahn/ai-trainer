"""Climb power from the app, not from the model (ai-trainer-ops#188).

With 75 kg in the profile, Lena's question ("Wie viel Watt … um unter 45
Minuten zu bleiben?", 14 km and 850 m) got 250–260, 255–265 and 285–300 W,
where about 308 W is needed: the coach worked it out from the FTP. The owner chose (ai-trainer-ops
#136) to have the app work it out and the coach quote it.
"""

import pytest

from services import climb_power
from services.prompts import ask_trainer_system_sections

LENA_RACE = "Bergzeitfahren Puig Major, 14 km, 850 Hm"
TODAY = "2026-10-22"


@pytest.mark.parametrize(("kg", "watts"), [(62, 264), (75, 308)])
def test_the_weight_moves_the_climb_power(kg, watts):
    assert round(climb_power.climb_watts(14, 850, 45, kg)) == watts
    assert climb_power.climb_minutes(14, 850, watts, kg) == pytest.approx(45, abs=0.1)


@pytest.mark.parametrize(
    ("text", "climb"),
    [
        (LENA_RACE, (14.0, 850)),
        ("Alpe d'Huez: 13,8 km und 1.071 Höhenmeter", (13.8, 1071)),
        ("12 km with 900 m of climbing", (12.0, 900)),
        ("letzte Woche 60 km, 1000 Hm gefahren; am Berg 14 km mit 850 Hm", (14.0, 850)),
        ("Gestern 60 km mit 1000 Hm. Am Berg dann 14 km mit 850 Hm?", (14.0, 850)),
        ("Ötztaler, 238 km, 5500 Hm", None),  # 2.3 %: a course, not one climb
        ("2 km mit 1500 Hm", None),  # 75 %: a typo
        ("Wie viel Watt am Berg?", None),
    ],
)
def test_a_climb_is_read_off_the_text(text, climb):
    assert climb_power.stated_climb(text) == climb


def test_the_known_climbs_are_the_chat_the_profile_and_the_calendar():
    climbs = climb_power.known_climbs(
        ["Und am Kesselberg, 5 km mit 300 Hm?", "Wie viel Watt?"],
        "2026-12-30",
        LENA_RACE,
        [
            {
                "date": "2026-12-30",
                "distanceKm": 14.0,
                "elevationM": 850,
            },  # the same race
            {
                "date": "2027-05-01",
                "distanceKm": 120.0,
                "elevationM": 2000,
            },  # not a climb
            {
                "date": "2026-06-01",
                "distanceKm": 21.0,
                "elevationM": 1600,
            },  # already ridden
            {"date": "2027-06-01", "distanceKm": 21.0, "elevationM": 1600},
        ],
        TODAY,
    )

    assert climbs == [
        ("a climb named in this chat", 5.0, 300),
        ("the race on 2026-12-30", 14.0, 850),
        ("the race on 2027-06-01", 21.0, 1600),
    ]


def test_a_race_already_ridden_is_not_a_climb_ahead():
    assert climb_power.known_climbs([], "2026-06-01", LENA_RACE, [], TODAY) == []


@pytest.mark.parametrize(
    ("kg", "row"), [(62, "45 min 264 W (4.3 W/kg)"), (75, "45 min 308 W (4.1 W/kg)")]
)
def test_the_coach_gets_the_row_for_the_target_time(kg, row):
    section = climb_power.prompt_section(
        [("the race on 2026-12-30", 14.0, 850)], kg, 280
    )

    assert row in section
    assert f"body weight of {kg} kg and a bike of 8 kg" in section
    assert "never work out climb watts yourself" in section


def test_the_time_at_the_ftp_is_named():
    section = climb_power.prompt_section(
        [("the race on 2026-12-30", 14.0, 850)], 62, 280
    )

    assert "At the FTP of 280 W: about 43 min." in section


def test_without_a_weight_there_are_no_figures():
    assert (
        climb_power.prompt_section([("the race on 2026-12-30", 14.0, 850)], None, 280)
        == ""
    )


def test_the_figures_reach_the_system_prompt():
    sections = ask_trainer_system_sections(
        {}, TODAY, [], [], "", "", "", "", climb_section="Climb power: 264 W"
    )

    assert sections["climb"] == "\n\nClimb power: 264 W"


@pytest.mark.asyncio
async def test_the_chat_works_out_the_profile_race(
    client, auth_headers, mock_ai_service
):
    await client.put(
        "/api/v1/users/me",
        headers=auth_headers,
        json={
            "weightKg": 62,
            "currentFTP": 280,
            "raceDate": "2099-12-30",
            "raceDescription": LENA_RACE,
        },
    )

    await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={
            "question": "Wie viel Watt sollte ich am Berg treten, um unter 45 Minuten zu bleiben?"
        },
    )

    section = mock_ai_service["ask_trainer"].call_args.kwargs["climb_section"]
    assert "the race on 2099-12-30, 14 km with 850 m (6.1 %)" in section
    assert "45 min 264 W (4.3 W/kg)" in section
