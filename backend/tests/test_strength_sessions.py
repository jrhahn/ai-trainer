"""Logging a gym session and reading what it produced (#714).

Cyclists already lift; the app planned as though they did not. A gym session was
a duration and a 1-5 effort, stored — until now — with ``sport_type``
hard-coded to cycling.

These exercise the whole round trip over HTTP: the sets go in with the workout
log, the derived figures come back out, the e1RM trend accumulates per exercise,
and the session's own sport is recorded rather than assumed.
"""

from __future__ import annotations

import pytest

from services.strength_model import FORMULA_BRZYCKI, e1rm

GYM_FEEDBACK = {
    "actualDurationMinutes": 58,
    "perceivedEffort": 4,
    "notes": "Squats felt heavy, pulls fine.",
    "completedAt": "2026-10-05T19:30:00Z",
}


def _session(date: str, sets: list[dict], *, sport: str = "strength") -> dict:
    return {
        "feedback": {**GYM_FEEDBACK, "completedAt": f"{date}T19:30:00Z"},
        "sport": sport,
        "strengthSets": sets,
    }


async def _log(client, headers, date: str, sets: list[dict], **kwargs) -> None:
    response = await client.post(
        f"/api/v1/users/me/workouts/{date}",
        headers=headers,
        json=_session(date, sets, **kwargs),
    )
    assert response.status_code == 200, response.text


# ---------------------------------------------------------------------------
# Logging and reading one session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_gym_session_logs_its_sets_and_yields_tonnage_and_e1rm(
    client, auth_headers
):
    """The first acceptance criterion, end to end."""
    await _log(
        client,
        auth_headers,
        "2026-10-05",
        [
            {"exercise": "Back Squat", "reps": 5, "weightKg": 100.0, "rir": 2},
            {"exercise": "Back Squat", "reps": 5, "weightKg": 100.0, "rir": 1},
            {"exercise": "Romanian Deadlift", "reps": 8, "weightKg": 80.0},
        ],
    )

    body = (
        await client.get(
            "/api/v1/users/me/workouts/2026-10-05/strength", headers=auth_headers
        )
    ).json()

    assert body["volumeLoadKg"] == pytest.approx(5 * 100 + 5 * 100 + 8 * 80)
    # Normalised to one spelling, so the trend cannot fragment across three.
    assert sorted(body["bestE1rmKg"]) == ["back squat", "romanian deadlift"]
    # The *first* of the two identical squat sets wins, and that is the right
    # way round: 5 reps at 100 kg with 2 still in reserve says the athlete could
    # have done 7, where the second set — same weight, same reps, only 1 left —
    # says 6. More reserve at the same load is evidence of a higher maximum, and
    # the fresher observation is the better one.
    assert body["bestE1rmKg"]["back squat"] == pytest.approx(
        round(e1rm(100.0, 5, rir=2), 1)
    )
    assert e1rm(100.0, 5, rir=2) > e1rm(100.0, 5, rir=1)
    assert len(body["sets"]) == 3
    # Sets are numbered within the exercise, in the order performed.
    squats = [s for s in body["sets"] if s["exercise"] == "back squat"]
    assert [s["setIndex"] for s in squats] == [1, 2]


@pytest.mark.asyncio
async def test_the_back_off_set_is_shown_as_a_fraction_of_the_top_set(
    client, auth_headers
):
    """What %e1RM is for: the same 100 kg is a warm-up for one lifter and a
    maximal effort for another, so an absolute weight says nothing portable.
    """
    await _log(
        client,
        auth_headers,
        "2026-10-06",
        [
            {"exercise": "Bench", "reps": 3, "weightKg": 100.0, "rir": 0},
            {"exercise": "Bench", "reps": 8, "weightKg": 75.0, "rir": 3},
        ],
    )

    sets = (
        await client.get(
            "/api/v1/users/me/workouts/2026-10-06/strength", headers=auth_headers
        )
    ).json()["sets"]

    top, back_off = sets[0], sets[1]
    # A 3-rep set taken to failure is ~90 % of a one-rep max, not 100 % of it —
    # which is the textbook figure and a useful check that %e1RM is relative to
    # the *estimated max* rather than to the heaviest weight on the day.
    assert 85.0 < top["relativeIntensityPct"] < 95.0
    assert 60.0 < back_off["relativeIntensityPct"] < 75.0
    assert back_off["relativeIntensityPct"] < top["relativeIntensityPct"]


@pytest.mark.asyncio
async def test_re_logging_a_session_corrects_it_instead_of_growing_it(
    client, auth_headers
):
    """A re-log is the athlete fixing the session — dropping a set they did not
    actually do. An upsert keyed on set index would leave the removed tail
    behind, so the session would silently grow every time it was edited down.
    """
    await _log(
        client,
        auth_headers,
        "2026-10-07",
        [
            {"exercise": "Squat", "reps": 5, "weightKg": 100.0},
            {"exercise": "Squat", "reps": 5, "weightKg": 100.0},
            {"exercise": "Squat", "reps": 5, "weightKg": 100.0},
        ],
    )
    await _log(
        client,
        auth_headers,
        "2026-10-07",
        [{"exercise": "Squat", "reps": 5, "weightKg": 100.0}],
    )

    body = (
        await client.get(
            "/api/v1/users/me/workouts/2026-10-07/strength", headers=auth_headers
        )
    ).json()
    assert len(body["sets"]) == 1
    assert body["volumeLoadKg"] == pytest.approx(500.0)


@pytest.mark.asyncio
async def test_an_empty_trailing_row_does_not_fail_the_whole_session(
    client, auth_headers
):
    """A form that submits a blank row is normal. Rejecting the session for it
    would lose the sets the athlete did fill in.
    """
    await _log(
        client,
        auth_headers,
        "2026-10-08",
        [
            {"exercise": "Squat", "reps": 5, "weightKg": 100.0},
            {"exercise": "", "reps": 0, "weightKg": 0.0},
        ],
    )

    body = (
        await client.get(
            "/api/v1/users/me/workouts/2026-10-08/strength", headers=auth_headers
        )
    ).json()
    assert len(body["sets"]) == 1


@pytest.mark.asyncio
async def test_a_session_with_no_sets_reads_back_empty_rather_than_failing(
    client, auth_headers
):
    """Reading a date the athlete never lifted on is a normal request — the
    client asks before it knows — and zero work moved is a true statement.
    """
    body = (
        await client.get(
            "/api/v1/users/me/workouts/2026-10-09/strength", headers=auth_headers
        )
    ).json()
    assert body["sets"] == []
    assert body["volumeLoadKg"] == 0.0
    assert body["bestE1rmKg"] == {}


@pytest.mark.asyncio
async def test_the_two_sessions_of_a_two_a_day_keep_their_own_sets(
    client, auth_headers
):
    """Gym in the morning, gym again in the evening. Session identity is
    ``(date, slot)`` (#496), so these must not merge into one.
    """
    for slot, weight in ((0, 100.0), (1, 60.0)):
        response = await client.post(
            "/api/v1/users/me/workouts/2026-10-10",
            headers=auth_headers,
            json={
                "feedback": {**GYM_FEEDBACK, "completedAt": "2026-10-10T19:30:00Z"},
                "sport": "strength",
                "slot": slot,
                "strengthSets": [
                    {"exercise": "Squat", "reps": 5, "weightKg": weight}
                ],
            },
        )
        assert response.status_code == 200, response.text

    morning = (
        await client.get(
            "/api/v1/users/me/workouts/2026-10-10/strength?slot=0",
            headers=auth_headers,
        )
    ).json()
    evening = (
        await client.get(
            "/api/v1/users/me/workouts/2026-10-10/strength?slot=1",
            headers=auth_headers,
        )
    ).json()

    assert morning["volumeLoadKg"] == pytest.approx(500.0)
    assert evening["volumeLoadKg"] == pytest.approx(300.0)


# ---------------------------------------------------------------------------
# The sport the log never used to record
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_logged_gym_session_is_not_stored_as_a_bike_ride(
    client, auth_headers
):
    """``WorkoutLog.sport_type`` defaulted to cycling and nothing ever passed it,
    so every logged session — gym ones included — was a bike ride as far as the
    database was concerned. That is also why its reported effort could not price
    it: the load ladder prices by sport.
    """
    await _log(client, auth_headers, "2026-10-11", [], sport="strength")

    logs = (await client.get("/api/v1/users/me/workouts", headers=auth_headers)).json()
    assert logs["2026-10-11"]["perceivedEffort"] == 4

    # And the stored row carries the sport, which is what the load chain reads.
    import crud
    from database import async_session_maker

    async with async_session_maker() as db:
        from sqlalchemy import select

        import models

        row = await db.scalar(
            select(models.WorkoutLog).where(models.WorkoutLog.date == "2026-10-11")
        )
        assert row is not None
        assert crud  # imported for symmetry with the other data-access tests
        assert row.sport_type == "strength"


# ---------------------------------------------------------------------------
# e1RM trends
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e1rm_trends_per_exercise_over_time(client, auth_headers):
    """The third acceptance criterion.

    One point per session, the best estimate that session produced — not every
    set, because a session's back-off sets would draw a sawtooth that reads as
    the athlete getting weaker and stronger within the hour.
    """
    for date, weight in (
        ("2026-09-01", 100.0),
        ("2026-09-08", 102.5),
        ("2026-09-15", 105.0),
    ):
        await _log(
            client,
            auth_headers,
            date,
            [
                {"exercise": "Back Squat", "reps": 5, "weightKg": weight, "rir": 2},
                # A deliberately submaximal back-off set on the same day.
                {"exercise": "Back Squat", "reps": 10, "weightKg": 70.0, "rir": 4},
            ],
        )

    body = (
        await client.get(
            "/api/v1/users/me/strength/e1rm/Back%20Squat", headers=auth_headers
        )
    ).json()

    assert body["exercise"] == "back squat"
    assert [p["date"] for p in body["points"]] == [
        "2026-09-01",
        "2026-09-08",
        "2026-09-15",
    ]
    estimates = [p["e1rmKg"] for p in body["points"]]
    assert estimates == sorted(estimates)
    # The top set is the evidence: each point came from the 5-rep set.
    assert all(p["reps"] == 5 for p in body["points"])
    assert all(p["confident"] for p in body["points"])


@pytest.mark.asyncio
async def test_the_formula_is_the_caller_s_choice_and_is_reported_back(
    client, auth_headers
):
    """Epley and Brzycki are regressions on different populations, so neither is
    "correct". The answer says which one produced it.

    15 reps, not 10: the two formulas coincide *exactly* at ten — ``36/(37−10)``
    is ``1 + 10/30`` — so a ten-rep set cannot tell them apart and would make
    this test pass whichever formula was actually used.
    """
    await _log(
        client,
        auth_headers,
        "2026-09-20",
        [{"exercise": "Squat", "reps": 15, "weightKg": 100.0}],
    )

    epley = (
        await client.get(
            "/api/v1/users/me/strength/e1rm/Squat", headers=auth_headers
        )
    ).json()
    brzycki = (
        await client.get(
            f"/api/v1/users/me/strength/e1rm/Squat?formula={FORMULA_BRZYCKI}",
            headers=auth_headers,
        )
    ).json()

    assert epley["formula"] != brzycki["formula"]
    assert epley["points"][0]["e1rmKg"] != brzycki["points"][0]["e1rmKg"]


@pytest.mark.asyncio
async def test_an_unknown_formula_is_refused_rather_than_silently_defaulted(
    client, auth_headers
):
    """Silently falling back would answer a question the caller did not ask, and
    label the answer with a formula that did not produce it.
    """
    response = await client.get(
        "/api/v1/users/me/strength/e1rm/Squat?formula=magic", headers=auth_headers
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_a_high_rep_estimate_is_returned_but_marked(client, auth_headers):
    """Beyond ~15 reps both formulas extrapolate past their fitted data. The
    estimate is still given — refusing it would discard the only evidence some
    sessions produce — but a trend line has to be able to show it differently.
    """
    await _log(
        client,
        auth_headers,
        "2026-09-21",
        [{"exercise": "Goblet Squat", "reps": 25, "weightKg": 30.0}],
    )

    points = (
        await client.get(
            "/api/v1/users/me/strength/e1rm/Goblet%20Squat", headers=auth_headers
        )
    ).json()["points"]

    assert len(points) == 1
    assert points[0]["e1rmKg"] > 30.0
    assert points[0]["confident"] is False


@pytest.mark.asyncio
async def test_an_exercise_never_logged_has_an_empty_trend(client, auth_headers):
    body = (
        await client.get(
            "/api/v1/users/me/strength/e1rm/Clean%20and%20Jerk", headers=auth_headers
        )
    ).json()
    assert body["points"] == []


@pytest.mark.asyncio
async def test_the_athlete_s_own_exercises_are_listed(client, auth_headers):
    """Their lifts, not a hardcoded list of exercises someone else thought they
    should be doing.
    """
    await _log(
        client,
        auth_headers,
        "2026-09-22",
        [
            {"exercise": "Back Squat", "reps": 5, "weightKg": 100.0},
            {"exercise": "bench press", "reps": 5, "weightKg": 80.0},
            {"exercise": "BACK SQUAT", "reps": 5, "weightKg": 100.0},
        ],
    )

    body = (
        await client.get(
            "/api/v1/users/me/strength/exercises", headers=auth_headers
        )
    ).json()
    # Three rows, two exercises: the two spellings of the squat are one lift.
    assert body["exercises"] == ["back squat", "bench press"]


@pytest.mark.asyncio
async def test_one_athlete_cannot_read_another_s_lifts(client, auth_headers):
    """Every query is scoped to the authenticated user; this pins it for the new
    endpoints rather than assuming the pattern held.
    """
    await _log(
        client,
        auth_headers,
        "2026-09-23",
        [{"exercise": "Squat", "reps": 5, "weightKg": 100.0}],
    )

    other = await client.post(
        "/api/v1/auth/register",
        json={
            "name": "Other Rider",
            "email": "other@example.com",
            "password": "Str0ng!Pass",
        },
    )
    other_headers = {"Authorization": f"Bearer {other.json()['access_token']}"}

    assert (
        await client.get(
            "/api/v1/users/me/strength/exercises", headers=other_headers
        )
    ).json()["exercises"] == []
    assert (
        await client.get(
            "/api/v1/users/me/workouts/2026-09-23/strength", headers=other_headers
        )
    ).json()["sets"] == []
    assert (
        await client.get(
            "/api/v1/users/me/strength/e1rm/Squat", headers=other_headers
        )
    ).json()["points"] == []


def test_the_gym_session_reuses_the_modality_that_already_existed():
    """#714 asks for the structured session to be wired to ``MODALITY_GYM``
    rather than for a parallel concept to be invented.

    It is: the sport recorded on the log is the same vocabulary
    ``modality_for_sport`` already reads, so the mapping resolves with nothing
    new added. The reason it never fired for a gym session before is that the
    log stored every session as cycling.
    """
    from services.activity_identity import SPORT_CYCLING, SPORT_STRENGTH
    from services.motivation_model import MODALITY_GYM
    from services.training_utility import modality_for_sport

    assert modality_for_sport(SPORT_STRENGTH) == MODALITY_GYM
    assert modality_for_sport("WeightTraining") == MODALITY_GYM
    assert modality_for_sport(SPORT_CYCLING) != MODALITY_GYM


@pytest.mark.asyncio
async def test_the_endpoints_require_authentication(client):
    for path in (
        "/api/v1/users/me/strength/exercises",
        "/api/v1/users/me/strength/e1rm/Squat",
        "/api/v1/users/me/workouts/2026-09-23/strength",
    ):
        assert (await client.get(path)).status_code in (401, 403)
