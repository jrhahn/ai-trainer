"""Entering a session that no plan day and no recording covers (ai-trainer-ops#47).

The only manual path used to be "Log Completed Workout" on a planned,
not-rest, not-yet-logged session. A ride on a rest day, a run when a ride was
planned, a second session, a day outside the plan: none could be entered, so
none reached the load chain, and fatigue was underestimated exactly where the
athlete trained more than planned.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from httpx import AsyncClient

import crud
import models
from services.activity_identity import SPORT_CYCLING, SPORT_RUNNING, SPORT_STRENGTH
from services.training_load import LOAD_SOURCE_RPE
from tests.conftest import TestSessionLocal

URL = "/api/v1/users/me/activities"
EMAIL = "rider@example.com"


def _entry(**overrides) -> dict:
    return {
        "date": (date.today() - timedelta(days=1)).isoformat(),
        "sport": "running",
        "durationMinutes": 45,
        "perceivedEffort": 3,
        "notes": "Easy loop.",
        **overrides,
    }


async def _metrics() -> list[models.RideMetric]:
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_email(db, EMAIL)
        return list(await crud.get_all_ride_metrics_ordered(db, user.id))


async def _logs() -> list[models.WorkoutLog]:
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_email(db, EMAIL)
        return await crud.get_workout_logs(db, user.id)


async def test_an_unplanned_run_becomes_a_session_with_a_load(
    client: AsyncClient, auth_headers
):
    response = await client.post(URL, headers=auth_headers, json=_entry())

    assert response.status_code == 201, response.text
    assert response.json()["sport"] == SPORT_RUNNING
    rows = await _metrics()
    assert len(rows) == 1
    assert rows[0].sport_type == SPORT_RUNNING
    assert rows[0].tss_source == LOAD_SOURCE_RPE
    assert rows[0].atl_after > 0
    assert (rows[0].ctl_by_sport or {}).get(SPORT_CYCLING, 0.0) == 0.0


async def test_it_needs_no_plan_at_all(client: AsyncClient, auth_headers):
    """The athlete without a plan, or on a day the plan never covered."""
    plan = await client.get("/api/v1/users/me/plan", headers=auth_headers)
    assert plan.json()["plan"] == []

    response = await client.post(URL, headers=auth_headers, json=_entry(sport="strength"))

    assert response.status_code == 201
    assert [m.sport_type for m in await _metrics()] == [SPORT_STRENGTH]


async def test_it_never_takes_a_planned_sessions_slot(client: AsyncClient, auth_headers):
    """A ride on a day with a planned session stays a session of its own.

    Slot 1 would be read as the plan's second session the next time the plan
    put one there; the entered range starts at 100 so no plan reaches it.
    """
    day = (date.today() - timedelta(days=1)).isoformat()
    plan = [{"date": day, "workoutType": "endurance", "title": "Ride",
             "durationMinutes": 60, "sport": "cycling"}]
    await client.put("/api/v1/users/me/plan", headers=auth_headers, json={"plan": plan})

    first = await client.post(URL, headers=auth_headers, json=_entry(date=day))
    second = await client.post(URL, headers=auth_headers, json=_entry(date=day, sport="strength"))

    assert (first.json()["slot"], second.json()["slot"]) == (100, 101)
    assert sorted(log.slot for log in await _logs()) == [100, 101]


async def test_two_entries_on_one_day_are_two_sessions(client: AsyncClient, auth_headers):
    await client.post(URL, headers=auth_headers, json=_entry())
    await client.post(URL, headers=auth_headers, json=_entry())

    assert len(await _metrics()) == 2


async def test_deleting_an_entry_removes_its_load(client: AsyncClient, auth_headers):
    created = (await client.post(URL, headers=auth_headers, json=_entry())).json()

    response = await client.delete(
        f"{URL}/{created['date']}/{created['slot']}", headers=auth_headers
    )

    assert response.status_code == 204
    assert await _logs() == []
    assert await _metrics() == []


async def test_a_planned_sessions_log_cannot_be_deleted_here(
    client: AsyncClient, auth_headers
):
    """Its log is feedback on a plan day; removing it would read as skipped."""
    day = (date.today() - timedelta(days=1)).isoformat()
    await client.post(
        f"/api/v1/users/me/workouts/{day}",
        headers=auth_headers,
        json={"feedback": {"actualDurationMinutes": 60, "perceivedEffort": 3,
                           "notes": "", "completedAt": f"{day}T08:00:00"}},
    )

    response = await client.delete(f"{URL}/{day}/0", headers=auth_headers)

    assert response.status_code == 404
    assert len(await _logs()) == 1


async def test_deleting_what_is_not_there_is_404(client: AsyncClient, auth_headers):
    response = await client.delete(f"{URL}/2026-01-01/100", headers=auth_headers)
    assert response.status_code == 404


@pytest.mark.parametrize(
    "bad",
    [
        {"durationMinutes": 0},
        {"perceivedEffort": 0},
        {"perceivedEffort": 6},
        {"date": "yesterday"},
        {"date": "2026-02-30"},
        {"averageHeartRate": 10},
    ],
)
async def test_an_unreadable_entry_is_refused(client: AsyncClient, auth_headers, bad):
    response = await client.post(URL, headers=auth_headers, json=_entry(**bad))
    assert response.status_code == 422
    assert await _logs() == []


async def test_an_activity_cannot_be_in_the_future(client: AsyncClient, auth_headers):
    later = (date.today() + timedelta(days=3)).isoformat()
    response = await client.post(URL, headers=auth_headers, json=_entry(date=later))
    assert response.status_code == 422


async def test_tomorrow_is_allowed_for_the_time_zone_ahead_of_the_server(
    client: AsyncClient, auth_headers
):
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    response = await client.post(URL, headers=auth_headers, json=_entry(date=tomorrow))
    assert response.status_code == 201


async def test_entering_needs_a_session(client: AsyncClient):
    assert (await client.post(URL, json=_entry())).status_code == 401


async def test_both_entries_reach_the_dashboards_list(client: AsyncClient, auth_headers):
    """The list the dashboard draws from had its own near-duplicate collapse.

    Found by the browser test, not by these: the rows were right, the list
    showed one of them.
    """
    await client.post(URL, headers=auth_headers, json=_entry())
    await client.post(URL, headers=auth_headers, json=_entry(durationMinutes=30))

    history = await client.get("/api/v1/users/me/ride-metrics-history", headers=auth_headers)

    rides = history.json()["rides"]
    assert sorted(r["externalActivityId"].split("#")[1] for r in rides) == ["100", "101"]
    assert {r["activitySource"] for r in rides} == {"logged"}


async def test_an_entered_run_never_ticks_the_planned_ride(client: AsyncClient, auth_headers):
    """"Add activity" says *this was something else*; matching must believe it.

    Found in the browser: a run entered on a day with one planned ride marked
    the ride "Done", for the dashboard and for the coach alike.
    """
    day = (date.today() - timedelta(days=1)).isoformat()
    plan = [{"date": day, "workoutType": "endurance", "title": "Ride",
             "durationMinutes": 60, "sport": "cycling"}]
    await client.put("/api/v1/users/me/plan", headers=auth_headers, json={"plan": plan})
    await client.post(URL, headers=auth_headers, json=_entry(date=day))

    rides = (
        await client.get("/api/v1/users/me/ride-metrics-history", headers=auth_headers)
    ).json()["rides"]

    assert len(rides) == 1
    assert rides[0]["planMatchStatus"] == "unmatched"
    assert rides[0]["matchedPlanDate"] is None


def test_only_the_unplanned_range_counts_as_an_entry():
    from services import logged_sessions

    def row(source: str, ident: str | None) -> models.RideMetric:
        return models.RideMetric(activity_source=source, external_activity_id=ident)

    assert logged_sessions.is_unplanned_entry(row("logged", "2026-10-01#100"))
    assert not logged_sessions.is_unplanned_entry(row("logged", "2026-10-01#0"))
    assert not logged_sessions.is_unplanned_entry(row("logged", "2026-10-01"))
    assert not logged_sessions.is_unplanned_entry(row("strava", "2026-10-01#100"))


async def test_an_entered_session_is_not_asked_what_it_was(client: AsyncClient, auth_headers):
    """The athlete said what it was by entering it; there was nothing to classify."""
    await client.post(URL, headers=auth_headers, json=_entry())
    rides = (
        await client.get("/api/v1/users/me/ride-metrics-history", headers=auth_headers)
    ).json()["rides"]
    assert rides[0]["purposeQuestionOpen"] is False


def test_the_schema_names_the_logged_source():
    import schemas
    from services import logged_sessions

    assert schemas.LOGGED_ACTIVITY_SOURCE == logged_sessions.LOGGED_SOURCE


# ---------------------------------------------------------------------------
# Editing an entry
# ---------------------------------------------------------------------------


def _correction(**overrides) -> dict:
    return {"sport": "running", "durationMinutes": 60, "perceivedEffort": 4, "notes": "Longer than I said.", **overrides}


async def test_an_entry_is_corrected_in_place(client: AsyncClient, auth_headers):
    created = (await client.post(URL, headers=auth_headers, json=_entry())).json()

    response = await client.put(
        f"{URL}/{created['date']}/{created['slot']}", headers=auth_headers, json=_correction()
    )

    assert response.status_code == 200, response.text
    logs = await _logs()
    assert len(logs) == 1
    assert (logs[0].actual_duration_minutes, logs[0].perceived_effort) == (60, 4)
    rows = await _metrics()
    assert len(rows) == 1, "a correction must not add a second session"
    assert rows[0].duration_seconds == 3600


async def test_the_sport_can_be_corrected_too(client: AsyncClient, auth_headers):
    created = (await client.post(URL, headers=auth_headers, json=_entry())).json()
    await client.put(
        f"{URL}/{created['date']}/{created['slot']}", headers=auth_headers,
        json=_correction(sport="strength"),
    )
    assert [m.sport_type for m in await _metrics()] == [SPORT_STRENGTH]


async def test_the_workouts_listing_says_which_sport_an_entry_was(client: AsyncClient, auth_headers):
    created = (await client.post(URL, headers=auth_headers, json=_entry(sport="strength"))).json()
    listing = (await client.get("/api/v1/users/me/workouts", headers=auth_headers)).json()
    assert listing[f"{created['date']}#{created['slot']}"]["sport"] == SPORT_STRENGTH


async def test_only_an_entered_activity_can_be_edited_here(client: AsyncClient, auth_headers):
    day = (date.today() - timedelta(days=1)).isoformat()
    await client.post(
        f"/api/v1/users/me/workouts/{day}",
        headers=auth_headers,
        json={"feedback": {"actualDurationMinutes": 60, "perceivedEffort": 3,
                           "notes": "", "completedAt": f"{day}T08:00:00"}},
    )
    assert (await client.put(f"{URL}/{day}/0", headers=auth_headers, json=_correction())).status_code == 404
    assert (await client.put(f"{URL}/{day}/100", headers=auth_headers, json=_correction())).status_code == 404


async def test_a_correction_is_validated_like_an_entry(client: AsyncClient, auth_headers):
    created = (await client.post(URL, headers=auth_headers, json=_entry())).json()
    response = await client.put(
        f"{URL}/{created['date']}/{created['slot']}", headers=auth_headers,
        json=_correction(perceivedEffort=9),
    )
    assert response.status_code == 422


async def test_a_correction_keeps_a_heart_rate_it_was_not_asked_to_change(
    client: AsyncClient, auth_headers
):
    """A field nobody sent is a field nobody corrected.

    The upsert replaces the whole row, so without this the UI — which offers no
    heart-rate control — would blank the figure of an entry created through the
    API with one. That figure feeds ``load_for_log``, so fixing a typo in the
    note would quietly re-price the session. Found in review on PR #795.
    """
    created = (
        await client.post(URL, headers=auth_headers, json=_entry(averageHeartRate=142))
    ).json()
    assert [log.average_heart_rate for log in await _logs()] == [142]

    response = await client.put(
        f"{URL}/{created['date']}/{created['slot']}",
        headers=auth_headers,
        json=_correction(notes="Fixed the typo."),
    )

    assert response.status_code == 200, response.text
    logs = await _logs()
    assert [log.average_heart_rate for log in logs] == [142]
    assert logs[0].notes == "Fixed the typo."


async def test_a_correction_can_still_clear_the_heart_rate_on_purpose(
    client: AsyncClient, auth_headers
):
    """Omission means "leave it"; an explicit null still means "remove it"."""
    created = (
        await client.post(URL, headers=auth_headers, json=_entry(averageHeartRate=142))
    ).json()

    await client.put(
        f"{URL}/{created['date']}/{created['slot']}",
        headers=auth_headers,
        json=_correction(averageHeartRate=None),
    )

    assert [log.average_heart_rate for log in await _logs()] == [None]


async def test_an_entry_cannot_be_corrected_through_another_day(
    client: AsyncClient, auth_headers
):
    """The slot alone is not the identity; the day is the other half (#496)."""
    created = (await client.post(URL, headers=auth_headers, json=_entry())).json()
    other_day = (date.today() - timedelta(days=4)).isoformat()

    response = await client.put(
        f"{URL}/{other_day}/{created['slot']}", headers=auth_headers, json=_correction()
    )

    assert response.status_code == 404
    assert [log.date for log in await _logs()] == [created["date"]]


# ---------------------------------------------------------------------------
# Distance (ai-trainer-ops#47)
# ---------------------------------------------------------------------------


async def test_a_run_can_say_how_far_it_went(client: AsyncClient, auth_headers):
    created = (
        await client.post(URL, headers=auth_headers, json=_entry(distanceKm=10.4))
    ).json()

    assert [log.distance_km for log in await _logs()] == [10.4]
    listing = (await client.get("/api/v1/users/me/workouts", headers=auth_headers)).json()
    assert listing[f"{created['date']}#{created['slot']}"]["distanceKm"] == 10.4


async def test_a_session_without_a_distance_stores_none_not_zero(
    client: AsyncClient, auth_headers
):
    """"They did not say" and "zero kilometres" are different claims (#579).

    Asserted on the load row as well as the log, because that is where it would
    do damage: an envelope saying ``distance_m: 0`` makes
    ``activity_run_distance_km`` answer 0.0 instead of ``None``, and the runner
    who enters no distances would then have a *measured* weekly mileage of zero
    rather than an unknown one. Found by a mutation that survived the first
    version of this test.
    """
    await client.post(URL, headers=auth_headers, json=_entry())

    assert [log.distance_km for log in await _logs()] == [None]
    (row,) = await _metrics()
    assert row.perf_signals is None


async def test_adding_a_distance_later_updates_the_stored_row(
    client: AsyncClient, auth_headers
):
    """The no-op short-circuit must not skip the write that adds one.

    ``_already_stored`` exists so an import of an athlete with hand-logged
    sessions does not rewrite and replay for nothing. It compares what the row
    says — so until it compared the envelope too, a run first entered without a
    distance kept its envelope-less row forever, and the distance the athlete
    added afterwards never reached their weekly mileage. Also found by a
    surviving mutation.
    """
    from services import run_durability

    created = (await client.post(URL, headers=auth_headers, json=_entry())).json()
    assert (await _metrics())[0].perf_signals is None

    await client.put(
        f"{URL}/{created['date']}/{created['slot']}",
        headers=auth_headers,
        json=_correction(
            sport="running", durationMinutes=45, perceivedEffort=3, distanceKm=8.5
        ),
    )

    (row,) = await _metrics()
    assert run_durability.activity_run_distance_km(row) == 8.5


async def test_an_entered_run_distance_reaches_the_running_exposure(
    client: AsyncClient, auth_headers
):
    """The point of the field, and the #745 failure one layer along.

    Weekly mileage is read from ``perf_signals["distance_m"]``, which is built
    from a stream — so an athlete who enters their runs by hand had *no* running
    exposure, and the durability ceiling handed them beginner figures however
    much they ran.
    """
    from services import run_durability, run_model

    await client.post(URL, headers=auth_headers, json=_entry(distanceKm=12.0))

    (row,) = await _metrics()
    assert run_model.is_run_signals(row.perf_signals), "must be marked as a run"
    assert run_durability.activity_run_distance_km(row) == 12.0
    # One point is not an envelope: Critical Speed needs a curve, and a hand
    # entry cannot provide one.
    assert "speed_curve" not in row.perf_signals


async def test_an_entered_distance_never_reprices_the_session(
    client: AsyncClient, auth_headers
):
    """Load-bearing, and the reason this is safe at all.

    ``run_model.mean_gap_speed`` will divide this distance by this duration, and
    the pace rung would then treat a raw average pace as a grade-adjusted one —
    the conversion #745 refused when it declined to offer a logged average power
    to the power rung. It is safe only because the pace rung is consulted for
    rows already priced by pace, and this row is priced by sRPE. If that ever
    changes, this test is what says so.
    """
    without = (await client.post(URL, headers=auth_headers, json=_entry())).json()
    priced_without = [(m.tss, m.tss_source) for m in await _metrics()]

    # Only the distance changes. `_correction()`'s defaults move the duration
    # and the effort too, which would re-price the session legitimately and tell
    # us nothing — the first version of this test did exactly that and failed on
    # its own fixture.
    await client.put(
        f"{URL}/{without['date']}/{without['slot']}",
        headers=auth_headers,
        json=_correction(
            sport="running",
            durationMinutes=45,
            perceivedEffort=3,
            distanceKm=15.0,
        ),
    )

    rows = await _metrics()
    assert [(m.tss, m.tss_source) for m in rows] == priced_without
    assert rows[0].tss_source == LOAD_SOURCE_RPE


async def test_a_strength_session_gets_no_running_envelope(
    client: AsyncClient, auth_headers
):
    """A blob without the sport marker would be read by the cycling engine."""
    await client.post(
        URL, headers=auth_headers, json=_entry(sport="strength", distanceKm=5.0)
    )

    (row,) = await _metrics()
    assert row.perf_signals is None


async def test_a_correction_keeps_a_distance_it_was_not_asked_to_change(
    client: AsyncClient, auth_headers
):
    """Same rule as the heart rate, and the same reason (review on PR #795)."""
    created = (
        await client.post(URL, headers=auth_headers, json=_entry(distanceKm=10.0))
    ).json()

    await client.put(
        f"{URL}/{created['date']}/{created['slot']}",
        headers=auth_headers,
        json=_correction(notes="Fixed the note."),
    )

    assert [log.distance_km for log in await _logs()] == [10.0]


async def test_a_correction_can_clear_a_distance_on_purpose(
    client: AsyncClient, auth_headers
):
    created = (
        await client.post(URL, headers=auth_headers, json=_entry(distanceKm=10.0))
    ).json()

    await client.put(
        f"{URL}/{created['date']}/{created['slot']}",
        headers=auth_headers,
        json=_correction(distanceKm=None),
    )

    assert [log.distance_km for log in await _logs()] == [None]


@pytest.mark.parametrize("bad", [0, -1, 1001])
async def test_an_implausible_distance_is_refused(client: AsyncClient, auth_headers, bad):
    response = await client.post(URL, headers=auth_headers, json=_entry(distanceKm=bad))

    assert response.status_code == 422


async def test_correcting_a_run_to_strength_keeps_the_distance_for_a_change_back(
    client: AsyncClient, auth_headers
):
    """A sport change does not throw the distance away, and that is deliberate.

    The dialog offers no distance for a strength session, so a correction to
    strength sends no distance key, and the "omission means leave it" rule keeps
    the stored one. Raised in review on PR #799 as worth pinning rather than
    leaving to be inferred.

    It is the right way round: the common case is an athlete who picked the wrong
    sport, and getting their distance back when they fix it is kinder than making
    them retype it. Nothing reads it meanwhile — the running envelope is built
    only for runs, so the strength row carries no mileage.
    """
    from services import run_durability

    created = (
        await client.post(URL, headers=auth_headers, json=_entry(distanceKm=9.0))
    ).json()
    url = f"{URL}/{created['date']}/{created['slot']}"
    unchanged = dict(durationMinutes=45, perceivedEffort=3)

    await client.put(
        url, headers=auth_headers, json=_correction(sport="strength", **unchanged)
    )

    assert [log.distance_km for log in await _logs()] == [9.0], "kept on the log"
    (row,) = await _metrics()
    assert row.perf_signals is None, "but no mileage while it is a strength session"

    # And it comes back when the sport does.
    await client.put(
        url, headers=auth_headers, json=_correction(sport="running", **unchanged)
    )

    (row,) = await _metrics()
    assert run_durability.activity_run_distance_km(row) == 9.0
