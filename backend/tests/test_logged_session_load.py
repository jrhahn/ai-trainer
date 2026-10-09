"""A session only the athlete recorded still carries load (#745).

The load ladder's two bottom rungs were built for sessions nothing measured, and
could only be reached by a session something *imported*. These pin the fix: a
hand-logged gym session now raises fatigue, is labelled with the rung that priced
it, never becomes cycling fitness, and stops existing the moment its recording
turns up.

The structural test at the end is the one that matters most over time — it
catches a sixth chain caller wired without the reconcile, which no behavioural
test of today's five would see.
"""

from __future__ import annotations

import pytest

import crud
import models
from auth import hash_password
from services import logged_sessions, metrics_service
from services.activity_identity import SPORT_CYCLING, SPORT_RUNNING, SPORT_STRENGTH
from services.training_load import (
    LOAD_SOURCE_DURATION,
    LOAD_SOURCE_HEART_RATE,
    LOAD_SOURCE_POWER,
    LOAD_SOURCE_RPE,
)
from tests.conftest import TestSessionLocal

HOUR = 3600

#: Distinguishes "not supplied" from "supplied as None", which matters for every
#: field whose absence is itself the thing under test.
_UNSET = object()


class _Log:
    """The fields ``logged_sessions`` reads off a workout log."""

    def __init__(
        self,
        date: str,
        *,
        sport: str = SPORT_STRENGTH,
        minutes: object = 60,
        effort: object = _UNSET,
        hr: object = _UNSET,
        slot: int = 0,
    ):
        self.date = date
        self.sport_type = sport
        self.actual_duration_minutes = minutes
        self.slot = slot
        if effort is not _UNSET:
            self.perceived_effort = effort
        if hr is not _UNSET:
            self.average_heart_rate = hr


class _Metric:
    """The fields the reconcile rule reads off a stored activity row."""

    def __init__(
        self,
        date: str,
        *,
        sport: str = SPORT_CYCLING,
        source: str = "strava",
        external_id: str | None = None,
    ):
        self.activity_date = date
        self.sport_type = sport
        self.activity_source = source
        self.external_activity_id = external_id


async def _create_user(email: str, **kwargs) -> str:
    async with TestSessionLocal() as db:
        user = models.User(
            email=email,
            name="Rider",
            hashed_password=hash_password("Str0ng!Pass"),
            is_onboarded=True,
            current_ftp=250,
            ai_provider="gemini",
            **kwargs,
        )
        db.add(user)
        await db.flush()
        await db.commit()
        return user.id


async def _log_session(
    user_id: str,
    date: str,
    *,
    sport: str = SPORT_STRENGTH,
    minutes: int = 60,
    effort: int = 0,
    slot: int = 0,
    hr: int | None = None,
) -> None:
    async with TestSessionLocal() as db:
        await crud.upsert_workout_log(
            db,
            user_id,
            date,
            slot=slot,
            actual_duration_minutes=minutes,
            perceived_effort=effort,
            average_power=None,
            average_heart_rate=hr,
            peak_power=None,
            notes="",
            completed_at=f"{date}T18:00:00",
            sport_type=sport,
        )
        await db.commit()


async def _reconcile(user_id: str) -> int:
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        changed = await logged_sessions.reconcile_logged_sessions(db, user)
        await db.commit()
        return changed


async def _rows(user_id: str) -> list[models.RideMetric]:
    async with TestSessionLocal() as db:
        return list(await crud.get_all_ride_metrics_ordered(db, user_id))


async def _import_activity(
    user_id: str,
    date: str,
    *,
    sport: str,
    activity_id: int,
    tss: float = 80.0,
) -> None:
    """A recording arriving for a date, the way an importer would write it.

    The chain is replayed afterwards because every importer builds one: without
    it the row carries a load but no ledger, and a test comparing CTL before and
    after would be comparing two ``None``s.
    """
    async with TestSessionLocal() as db:
        await crud.upsert_ride_metric(
            db,
            user_id,
            strava_activity_id=activity_id,
            activity_source="strava",
            external_activity_id=str(activity_id),
            activity_date=date,
            sport_type=sport,
            duration_seconds=HOUR,
            tss=tss,
            tss_source=LOAD_SOURCE_POWER,
        )
        metrics_service.replay_load_chain(
            await crud.get_all_ride_metrics_ordered(db, user_id)
        )
        await db.commit()


# ---------------------------------------------------------------------------
# The acceptance criteria
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_logged_gym_session_raises_fatigue():
    """The headline: the day stops reading as rest.

    Before #745 this athlete's hour in the gym produced no row at all, so the
    freshness curve recovered across it and TSB *rose* — the #579 failure, still
    shipping for every session nobody recorded.
    """
    user_id = await _create_user("gym-fatigue@example.com")
    await _log_session(user_id, "2026-03-02", effort=4)

    assert await _reconcile(user_id) == 1

    rows = await _rows(user_id)
    assert len(rows) == 1
    assert rows[0].tss is not None and rows[0].tss > 0
    assert rows[0].atl_after is not None and rows[0].atl_after > 0
    # Fatigue up and form down across the session, which is what an hour of
    # training should look like.
    assert rows[0].tsb_after is not None and rows[0].tsb_after < 0


@pytest.mark.asyncio
async def test_the_load_is_labelled_with_the_rung_that_priced_it():
    """Never presented as a measurement: #712's whole discipline."""
    user_id = await _create_user("rung-label@example.com")
    await _log_session(user_id, "2026-03-02", effort=4)
    await _reconcile(user_id)

    rows = await _rows(user_id)
    assert rows[0].tss_source == LOAD_SOURCE_RPE


@pytest.mark.asyncio
async def test_a_session_with_no_effort_still_counts_from_its_duration():
    """The commonest log: saved without touching the RPE control.

    Requiring an effort would have left this one contributing nothing, which is
    the bug rather than a conservative version of the fix.
    """
    user_id = await _create_user("duration-rung@example.com")
    await _log_session(user_id, "2026-03-02", effort=0)
    await _reconcile(user_id)

    rows = await _rows(user_id)
    assert len(rows) == 1
    assert rows[0].tss_source == LOAD_SOURCE_DURATION
    assert rows[0].tss is not None and rows[0].tss > 0


@pytest.mark.asyncio
async def test_a_gym_session_never_becomes_cycling_fitness():
    """#713's invariant, restated for a session that was never imported."""
    user_id = await _create_user("no-cycling-ctl@example.com")
    await _log_session(user_id, "2026-03-02", effort=4)
    await _reconcile(user_id)

    rows = await _rows(user_id)
    by_sport = rows[0].ctl_by_sport or {}
    assert by_sport.get(SPORT_CYCLING, 0.0) == 0.0
    assert by_sport.get(SPORT_STRENGTH, 0.0) > 0.0
    assert rows[0].atl_after > 0.0


@pytest.mark.asyncio
async def test_the_recording_arriving_later_leaves_one_session():
    """The double-count the retirement rule exists to prevent.

    The athlete logs the gym session on Monday evening; the watch upload lands on
    Tuesday. Two rows for one session would price that hour twice.
    """
    user_id = await _create_user("one-session@example.com")
    await _log_session(user_id, "2026-03-02", effort=4)
    await _reconcile(user_id)
    assert len(await _rows(user_id)) == 1

    await _import_activity(
        user_id, "2026-03-02", sport="WeightTraining", activity_id=9001
    )
    assert await _reconcile(user_id) == 1  # the placeholder retired

    rows = await _rows(user_id)
    assert len(rows) == 1
    assert rows[0].activity_source == "strava"
    assert not logged_sessions.is_logged_placeholder(rows[0])


@pytest.mark.asyncio
async def test_re_saving_the_log_corrects_the_figure_rather_than_adding_a_session():
    """The identity is ``(date, slot)``, so the second save is an update."""
    user_id = await _create_user("resave@example.com")
    await _log_session(user_id, "2026-03-02", effort=2)
    await _reconcile(user_id)
    first = (await _rows(user_id))[0].tss

    await _log_session(user_id, "2026-03-02", effort=5)
    await _reconcile(user_id)

    rows = await _rows(user_id)
    assert len(rows) == 1
    assert rows[0].tss != first
    assert rows[0].tss > first


@pytest.mark.asyncio
async def test_a_log_with_no_duration_creates_nothing():
    """No duration is no claim that a session happened.

    The ladder returns ``None`` without one, and recording a zero would be the
    assertion that the athlete rested (#579).
    """
    user_id = await _create_user("no-duration@example.com")
    await _log_session(user_id, "2026-03-02", minutes=0, effort=4)

    assert await _reconcile(user_id) == 0
    assert await _rows(user_id) == []


@pytest.mark.asyncio
async def test_a_two_a_day_keeps_both_sessions():
    """#496's identity, which is why the key is not the date alone."""
    user_id = await _create_user("two-a-day@example.com")
    await _log_session(user_id, "2026-03-02", sport=SPORT_STRENGTH, effort=4, slot=0)
    await _log_session(
        user_id, "2026-03-02", sport=SPORT_CYCLING, minutes=90, effort=3, slot=1
    )

    assert await _reconcile(user_id) == 2
    rows = await _rows(user_id)
    assert len(rows) == 2
    assert {r.sport_type for r in rows} == {SPORT_STRENGTH, SPORT_CYCLING}


@pytest.mark.asyncio
async def test_a_two_a_day_in_one_sport_keeps_both_sessions():
    """Two runs on one day are two sessions, not one near-duplicate.

    Logged sessions share a name and carry no start time, so the importer's
    fuzzy match used to take the second for the first and overwrite it
    (ai-trainer-ops#47). Different sports never collided, which is why the
    test above did not see it.
    """
    user_id = await _create_user("two-runs@example.com")
    await _log_session(user_id, "2026-03-02", sport=SPORT_RUNNING, minutes=40, effort=3, slot=0)
    await _log_session(user_id, "2026-03-02", sport=SPORT_RUNNING, minutes=40, effort=3, slot=1)

    await _reconcile(user_id)
    rows = await _rows(user_id)
    assert len(rows) == 2
    assert all(r.sport_type == SPORT_RUNNING for r in rows)


def test_the_exact_identity_sources_name_the_logged_source():
    assert logged_sessions.LOGGED_SOURCE in crud.EXACT_IDENTITY_SOURCES


@pytest.mark.asyncio
async def test_a_recorded_session_is_left_to_the_importer():
    """Where an activity exists, ``apply_reported_effort`` owns the re-price.

    Writing a placeholder next to it would be the second place that prices one
    session, which is what the single load gate exists to prevent.
    """
    user_id = await _create_user("already-recorded@example.com")
    await _import_activity(
        user_id, "2026-03-02", sport="WeightTraining", activity_id=9002
    )
    await _log_session(user_id, "2026-03-02", effort=4)

    assert await _reconcile(user_id) == 0
    rows = await _rows(user_id)
    assert len(rows) == 1
    assert rows[0].activity_source == "strava"


@pytest.mark.asyncio
async def test_a_ride_recorded_that_day_does_not_cover_the_gym_session():
    """Matched on sport, not just date — the two-a-day this app already models.

    A cycling activity on the same date says nothing about whether the lifting
    happened, and treating it as cover would silently drop the gym load.
    """
    user_id = await _create_user("sport-matters@example.com")
    await _import_activity(user_id, "2026-03-02", sport="Ride", activity_id=9003)
    await _log_session(user_id, "2026-03-02", sport=SPORT_STRENGTH, effort=4, slot=1)

    assert await _reconcile(user_id) == 1
    rows = await _rows(user_id)
    assert len(rows) == 2
    placeholders = [r for r in rows if logged_sessions.is_logged_placeholder(r)]
    assert len(placeholders) == 1
    assert placeholders[0].sport_type == SPORT_STRENGTH


@pytest.mark.asyncio
async def test_the_second_reconcile_is_a_no_op():
    """Not merely idempotent — it does no work at all the second time.

    This runs on every import, so an athlete with any hand-logged session would
    otherwise re-write that row and replay the whole ledger on every sync
    forever, to arrive back exactly where it started.
    """
    user_id = await _create_user("idempotent@example.com")
    await _log_session(user_id, "2026-03-02", effort=4)
    assert await _reconcile(user_id) == 1
    first = [(r.id, r.tss, r.atl_after) for r in await _rows(user_id)]

    assert await _reconcile(user_id) == 0
    assert [(r.id, r.tss, r.atl_after) for r in await _rows(user_id)] == first


@pytest.mark.asyncio
async def test_the_placeholder_does_not_disturb_the_ledger_of_a_real_ride():
    """A hand-logged gym day between two rides leaves cycling CTL alone."""
    user_id = await _create_user("ledger-intact@example.com")
    await _import_activity(
        user_id, "2026-03-01", sport="Ride", activity_id=9101, tss=100.0
    )
    await _import_activity(
        user_id, "2026-03-03", sport="Ride", activity_id=9102, tss=100.0
    )
    before = {r.activity_date: r for r in await _rows(user_id)}
    cycling_before = {
        date: row.ctl_by_sport.get(SPORT_CYCLING) for date, row in before.items()
    }
    atl_before = before["2026-03-03"].atl_after

    await _log_session(user_id, "2026-03-02", sport=SPORT_STRENGTH, effort=4)
    await _reconcile(user_id)

    rows = {r.activity_date: r for r in await _rows(user_id)}
    assert len(rows) == 3
    for date, ctl in cycling_before.items():
        assert rows[date].ctl_by_sport.get(SPORT_CYCLING) == ctl
    # And the gym day carried into the ride that followed it: the fatigue the
    # second ride starts from is higher than it was with the day reading as rest.
    assert rows["2026-03-03"].atl_after > atl_before


# ---------------------------------------------------------------------------
# Over HTTP, which is the only shape production uses
# ---------------------------------------------------------------------------

GYM_FEEDBACK = {
    "actualDurationMinutes": 58,
    "perceivedEffort": 4,
    "notes": "Squats felt heavy.",
    "completedAt": "2026-10-05T19:30:00Z",
}


async def _metrics_for(email: str) -> list[models.RideMetric]:
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_email(db, email)
        assert user is not None
        return list(await crud.get_all_ride_metrics_ordered(db, user.id))


@pytest.mark.asyncio
async def test_logging_a_gym_session_over_http_gives_it_a_load(client, auth_headers):
    """The acceptance criterion through the route the browser calls.

    Asserted here and not only against ``reconcile_logged_sessions`` because a
    fix exercised through a call shape no caller uses reports itself done while
    the bug ships (#718). The route is the shape that matters: it is where the
    athlete's save actually lands.
    """
    plan = [
        {
            "date": "2026-10-05",
            "workoutType": "strength",
            "sport": "strength",
            "title": "Lower body",
            "description": "Squats and pulls",
            "durationMinutes": 60,
        }
    ]
    saved = await client.put(
        "/api/v1/users/me/plan", headers=auth_headers, json={"plan": plan}
    )
    assert saved.status_code == 200, saved.text

    logged = await client.post(
        "/api/v1/users/me/workouts/2026-10-05",
        headers=auth_headers,
        json={"feedback": GYM_FEEDBACK},
    )
    assert logged.status_code == 200, logged.text

    rows = await _metrics_for("rider@example.com")
    assert len(rows) == 1, "the logged gym session got no row in the load chain"
    assert rows[0].sport_type == SPORT_STRENGTH
    assert rows[0].tss_source == LOAD_SOURCE_RPE
    assert rows[0].tss > 0
    assert rows[0].atl_after > 0
    assert (rows[0].ctl_by_sport or {}).get(SPORT_CYCLING, 0.0) == 0.0


@pytest.mark.asyncio
async def test_re_saving_over_http_corrects_the_one_session(client, auth_headers):
    """Two saves of one session are one session, at the corrected figure."""
    first = await client.post(
        "/api/v1/users/me/workouts/2026-10-06",
        headers=auth_headers,
        json={
            "feedback": {
                **GYM_FEEDBACK,
                "perceivedEffort": 2,
                "completedAt": "2026-10-06T19:30:00Z",
            },
            "sport": "strength",
        },
    )
    assert first.status_code == 200, first.text
    easy = (await _metrics_for("rider@example.com"))[0].tss

    second = await client.post(
        "/api/v1/users/me/workouts/2026-10-06",
        headers=auth_headers,
        json={
            "feedback": {
                **GYM_FEEDBACK,
                "perceivedEffort": 5,
                "completedAt": "2026-10-06T19:30:00Z",
            },
            "sport": "strength",
        },
    )
    assert second.status_code == 200, second.text

    rows = await _metrics_for("rider@example.com")
    assert len(rows) == 1
    assert rows[0].tss > easy


@pytest.mark.asyncio
async def test_a_failure_to_reconcile_never_loses_the_athletes_log(
    client, auth_headers, monkeypatch
):
    """The log is the athlete's work; the load figure is ours.

    Guarded separately from the re-price above, so one failing cannot skip the
    other.
    """
    async def _boom(*args, **kwargs):
        raise RuntimeError("ledger exploded")

    monkeypatch.setattr(
        logged_sessions, "reconcile_logged_sessions", _boom, raising=True
    )

    logged = await client.post(
        "/api/v1/users/me/workouts/2026-10-07",
        headers=auth_headers,
        json={"feedback": {**GYM_FEEDBACK, "completedAt": "2026-10-07T19:30:00Z"}},
    )
    assert logged.status_code == 200, logged.text

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_email(db, "rider@example.com")
        log = await crud.get_workout_log_by_date(db, user.id, "2026-10-07", 0)
        assert log is not None


@pytest.mark.asyncio
async def test_a_new_ftp_does_not_delete_the_logged_sessions_load():
    """#579 again, by the route a new feature would reopen it.

    Changing FTP re-walks every row. A row priced by the ladder's lower rungs has
    no power to recompute from, and recomputing it from power anyway would set
    the load to ``None`` — putting the session back to entering the chain as a
    rest day. The existing chain rebuild gets this right; this pins it for the
    one row type that has no imported activity behind it at all.
    """
    user_id = await _create_user("ftp-change@example.com")
    await _log_session(user_id, "2026-03-02", effort=4)
    await _reconcile(user_id)
    before = (await _rows(user_id))[0]
    priced, source = before.tss, before.tss_source

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        await metrics_service.recalculate_metrics_for_user(db, user, ftp_override=300)
        await db.commit()

    after = (await _rows(user_id))[0]
    assert after.tss == priced
    assert after.tss_source == source
    assert after.atl_after > 0


# ---------------------------------------------------------------------------
# The rule itself, without a database
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("date", "slot", "expected"),
    [
        ("2026-03-02", 0, "2026-03-02#0"),
        ("2026-03-02", 1, "2026-03-02#1"),
        ("2026-03-02", None, "2026-03-02#0"),
    ],
)
def test_the_external_id_is_the_session_identity(date, slot, expected):
    assert logged_sessions.session_external_id(date, slot) == expected


@pytest.mark.parametrize(
    ("minutes", "claims"),
    [
        (60, True),
        (1, True),
        # One whole second is the floor, because seconds are what the ladder
        # prices in. Anything that rounds below it claims no session.
        (1 / 60, True),
        (0.004, False),
        (0, False),
        (None, False),
        (-30, False),
        ("not a number", False),
    ],
)
def test_only_a_duration_the_ladder_can_price_claims_a_session(minutes, claims):
    assert logged_sessions.asserts_a_session(_Log("2026-03-02", minutes=minutes)) is claims


def test_a_log_claiming_no_session_becomes_no_activity_and_no_load():
    """Both readers refuse the same log, rather than one of them inventing one."""
    empty = _Log("2026-03-02", minutes=0, effort=5)
    assert logged_sessions.logged_activity(empty) is None
    assert logged_sessions.load_for_log(empty) is None


async def _set_duration(user_id: str, date: str, minutes: float) -> None:
    """Write a duration the route's integer column could not hold."""
    async with TestSessionLocal() as db:
        log = await crud.get_workout_log_by_date(db, user_id, date, 0)
        assert log is not None
        log.actual_duration_minutes = minutes
        await db.commit()


@pytest.mark.asyncio
async def test_a_duration_that_rounds_to_no_seconds_is_not_written():
    """Where the duration test and the ladder used to disagree.

    A quarter of a second is a positive number of minutes but zero seconds, and
    the ladder prices in seconds — it returns ``None`` rather than a zero,
    because a zero is the claim that the athlete rested (#579). Both now read the
    same whole seconds, so such a log claims no session and gets no row.
    """
    user_id = await _create_user("rounds-to-zero@example.com")
    await _log_session(user_id, "2026-03-02", effort=4)
    await _set_duration(user_id, "2026-03-02", 0.004)

    assert await _reconcile(user_id) == 0
    assert await _rows(user_id) == []


@pytest.mark.asyncio
async def test_correcting_a_session_to_nothing_retires_the_row_it_wrote():
    """Found in review. The athlete's latest account has to win.

    While ``asserts_a_session`` accepted positive *minutes* and the ladder
    priced in *seconds*, a log corrected to a quarter of a second was still
    wanted — so the retirement rule left the row alone, the write was skipped for
    want of a price, and the stale load stood forever. The two tests agreeing is
    what fixes it; this pins the behaviour the disagreement broke.
    """
    user_id = await _create_user("corrected-to-nothing@example.com")
    await _log_session(user_id, "2026-03-02", effort=4)
    assert await _reconcile(user_id) == 1
    assert len(await _rows(user_id)) == 1

    await _set_duration(user_id, "2026-03-02", 0.004)

    assert await _reconcile(user_id) == 1  # the row retired
    assert await _rows(user_id) == []


def test_a_log_with_no_date_claims_nothing():
    """Defensive: the column is not nullable, so this is unreachable by a route.

    Worth the two lines anyway, because the alternative is an activity whose
    ``activity_date`` is the empty string sitting in the load chain forever.
    """
    assert logged_sessions.logged_activity(_Log("", minutes=60)) is None


def test_heart_rate_outranks_the_reported_effort():
    """The ladder's order, not this module's choice (#712).

    A monitor is a measurement of the response; an RPE is an opinion about it.
    """
    load = logged_sessions.load_for_log(
        _Log("2026-03-02", effort=5, hr=140),
        max_heart_rate=190,
        resting_heart_rate=50,
    )
    assert load is not None
    assert load.source == LOAD_SOURCE_HEART_RATE


def test_a_heart_rate_with_no_zones_falls_back_to_the_effort():
    """hrTSS needs the athlete's bounds; without them the rung cannot answer."""
    load = logged_sessions.load_for_log(_Log("2026-03-02", effort=4, hr=140))
    assert load is not None
    assert load.source == LOAD_SOURCE_RPE


def test_a_logged_average_power_is_not_offered_to_the_power_rung():
    """Deliberate: an average is not a normalised power.

    Converting one to the other is a formula this app does not have, and
    inventing it at a new site is what the single load gate exists to prevent.
    The session still gets a load — from a rung that admits to estimating.
    """
    log = _Log("2026-03-02", sport=SPORT_CYCLING, effort=3)
    log.average_power = 240

    load = logged_sessions.load_for_log(log)
    assert load is not None
    assert load.source != LOAD_SOURCE_POWER
    assert not load.is_measured


def test_the_module_knows_no_load_formula():
    """Structural: pricing belongs to the ladder, booking to the ledger loop.

    Asserted by reading the source because the failure mode is a *new* formula
    appearing here — the thing #712 and #713 were built to stop — and no
    behavioural test of today's two gates would notice it.
    """
    import pathlib

    source = (
        pathlib.Path(logged_sessions.__file__).read_text().split('"""', 2)[-1]
    )
    for formula in ("3600", "/ 3600", "** 2", "* 100", "0.95"):
        assert formula not in source, (
            f"a load formula appeared in logged_sessions: {formula!r}. "
            "Pricing belongs to services.training_load (#712)."
        )


# ---------------------------------------------------------------------------
# The pairing rule
# ---------------------------------------------------------------------------


def test_a_recording_covers_the_lower_slot_first():
    """Two logged gym sessions, one recorded: one placeholder, not two or none.

    The pairing can only be wrong about *which* of two same-sport sessions on a
    day was recorded, never about how many sessions the day held — and the count
    is what the load chain reads.
    """
    logs = [
        _Log("2026-03-02", slot=0, effort=4),
        _Log("2026-03-02", slot=1, effort=3),
    ]
    plan = logged_sessions.reconcile(
        logs, [_Metric("2026-03-02", sport="WeightTraining")]
    )
    assert [log.slot for log in plan.write] == [1]
    assert plan.retire == ()


def test_every_logged_session_is_covered_once_both_were_recorded():
    logs = [
        _Log("2026-03-02", slot=0, effort=4),
        _Log("2026-03-02", slot=1, effort=3),
    ]
    metrics = [
        _Metric("2026-03-02", sport="WeightTraining"),
        _Metric("2026-03-02", sport="strength"),
    ]
    assert logged_sessions.reconcile(logs, metrics).write == ()


def test_a_placeholder_whose_log_lost_its_duration_is_retired():
    """The log is the only reason the row exists, so it is also its only warrant."""
    stale = _Metric(
        "2026-03-02",
        sport=SPORT_STRENGTH,
        source=logged_sessions.LOGGED_SOURCE,
        external_id="2026-03-02#0",
    )
    plan = logged_sessions.reconcile([_Log("2026-03-02", minutes=0)], [stale])
    assert plan.write == ()
    assert plan.retire == (stale,)


def test_an_existing_placeholder_is_not_counted_as_its_own_recording():
    """The trap in the counting rule.

    A placeholder is an activity row like any other, so counting it as cover
    would make the second reconcile retire the row the first one wrote — the load
    would appear and disappear on alternate syncs.
    """
    mine = _Metric(
        "2026-03-02",
        sport=SPORT_STRENGTH,
        source=logged_sessions.LOGGED_SOURCE,
        external_id="2026-03-02#0",
    )
    plan = logged_sessions.reconcile([_Log("2026-03-02", effort=4)], [mine])
    assert len(plan.write) == 1
    assert plan.retire == ()


def test_an_empty_reconciliation_is_falsy():
    """What lets the common path skip the chain replay entirely."""
    assert not logged_sessions.reconcile([], [])
    assert logged_sessions.reconcile([_Log("2026-03-02", effort=4)], [])


# ---------------------------------------------------------------------------
# The guard that outlives today's call sites
# ---------------------------------------------------------------------------


def test_every_chain_caller_reconciles_the_logged_sessions():
    """A sixth importer wired without this double-counts, silently.

    The same failure ``test_every_chain_caller_feeds_the_reported_effort`` exists
    for, and the same reason it is asserted structurally: the failure mode is a
    *new* caller, which no behavioural test of today's five would catch. An
    importer that writes an activity without reconciling leaves the placeholder
    it supersedes in place, and that hour is then priced twice.
    """
    import pathlib

    backend = pathlib.Path(__file__).resolve().parent.parent
    missing: list[str] = []
    for path in sorted(backend.rglob("*.py")):
        if {"tests", "alembic", ".venv"} & set(path.parts):
            continue
        source = path.read_text()
        if "build_ride_metrics_chain(" not in source:
            continue
        if path.name == "analysis.py":
            continue  # where it is defined
        if "reconcile_logged_sessions" not in source:
            missing.append(str(path.relative_to(backend)))

    assert not missing, (
        "these build the load chain without reconciling the sessions only the "
        f"athlete recorded (#745): {missing}"
    )


def test_the_replay_is_the_one_ledger_loop():
    """#713's discipline: this module must not advance a ledger itself."""
    import inspect

    source = inspect.getsource(logged_sessions.reconcile_logged_sessions)
    assert "replay_load_chain" in source
    assert "LoadLedger" not in source
    assert "advance(" not in source


def test_metrics_service_is_imported_rather_than_reimplemented():
    assert logged_sessions.metrics_service is metrics_service
