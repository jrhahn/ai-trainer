"""GPX and TCX come in with an estimated load, and the upload says what it rests on (ai-trainer-ops#311).

They were refused since #48: a track and maybe heart rate, no device summary.
The owner, 2026-10-26: "wir schätzen die belastung, falls es geht, und weisen
den user darauf hin". The file becomes the activity a .fit becomes, and the
load chain prices it from what it carries.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select

import models
from services import track_import
from tests.conftest import TestSessionLocal

BULK = "/api/v1/users/me/upload-fit/bulk"
SINGLE = "/api/v1/users/me/upload-fit"

# Long past, so no upload here is recent enough for the model's read.
START = datetime(2026, 9, 1, 7, 0, tzinfo=timezone.utc)


def _stamp(i: int) -> str:
    return (START + timedelta(seconds=10 * i)).isoformat().replace("+00:00", "Z")


def _gpx(
    minutes: int = 30,
    *,
    sport: str = "cycling",
    heart_rate: int | None = 140,
    power: int | None = None,
    name: str = "Morning Ride",
    with_time: bool = True,
) -> bytes:
    points = []
    for i in range(minutes * 6 + 1):
        inner = f"<gpxtpx:hr>{heart_rate}</gpxtpx:hr>" if heart_rate else ""
        watts = f"<power>{power}</power>" if power else ""
        ext = (
            f"<extensions>{watts}<gpxtpx:TrackPointExtension>{inner}"
            "</gpxtpx:TrackPointExtension></extensions>"
            if heart_rate or power
            else ""
        )
        time = f"<time>{_stamp(i)}</time>" if with_time else ""
        points.append(
            f'<trkpt lat="{47.99 + i * 0.0007:.6f}" lon="7.85">'
            f"<ele>{300 + i * 0.1:.1f}</ele>{time}{ext}</trkpt>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1" '
        'xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">'
        f"<trk><name>{name}</name><type>{sport}</type><trkseg>{''.join(points)}"
        "</trkseg></trk></gpx>"
    ).encode()


def _tcx(
    minutes: int = 30,
    *,
    sport: str = "Biking",
    heart_rate: int | None = None,
    watts: int | None = None,
    timer_seconds: int | None = None,
) -> bytes:
    points = []
    for i in range(minutes * 6 + 1):
        hr = (
            f"<HeartRateBpm><Value>{heart_rate}</Value></HeartRateBpm>"
            if heart_rate
            else ""
        )
        ext = (
            f"<Extensions><ns3:TPX><ns3:Watts>{watts}</ns3:Watts></ns3:TPX></Extensions>"
            if watts
            else ""
        )
        points.append(
            f"<Trackpoint><Time>{_stamp(i)}</Time><Position>"
            f"<LatitudeDegrees>{47.99 + i * 0.0007:.6f}</LatitudeDegrees>"
            "<LongitudeDegrees>7.85</LongitudeDegrees></Position>"
            f"<AltitudeMeters>300</AltitudeMeters><DistanceMeters>{i * 80.0}</DistanceMeters>"
            f"{hr}{ext}</Trackpoint>"
        )
    timer = timer_seconds if timer_seconds is not None else minutes * 60
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2" '
        'xmlns:ns3="http://www.garmin.com/xmlschemas/ActivityExtension/v2">'
        f'<Activities><Activity Sport="{sport}"><Id>{_stamp(0)}</Id>'
        f'<Lap StartTime="{_stamp(0)}"><TotalTimeSeconds>{timer}</TotalTimeSeconds>'
        f"<Track>{''.join(points)}</Track></Lap></Activity></Activities>"
        "</TrainingCenterDatabase>"
    ).encode()


# ---------------------------------------------------------------------------
# Reading the files
# ---------------------------------------------------------------------------


def test_a_gpx_with_heart_rate_becomes_a_ride_with_its_streams():
    parsed = track_import.parse_track_activity(_gpx(), "morning.gpx")

    assert parsed.sport_type == "cycling"
    assert (parsed.duration_seconds, parsed.duration_minutes) == (1800, 30)
    assert (parsed.ride_date, parsed.completed_at) == (
        "2026-09-01",
        "2026-09-01T07:00:00+00:00",
    )
    assert parsed.activity_name == "Morning Ride"
    assert (parsed.avg_hr, parsed.avg_power) == (140, None)
    assert (parsed.start_lat, parsed.start_lng) == (47.99, 7.85)
    assert set(parsed.streams) == {
        "time",
        "heartrate",
        "heartrate_time",
        "altitude",
        "altitude_time",
        "velocity_smooth",
        "velocity_time",
        "latlng",
    }
    assert len(parsed.streams["heartrate"]["data"]) == 181
    assert parsed.streams["heartrate_time"]["data"][:3] == [0, 10, 20]
    # 0.0007° of latitude in ten seconds: about 7.8 m/s.
    assert all(7.7 < v < 7.9 for v in parsed.streams["velocity_smooth"]["data"])
    assert parsed.source_metadata["file_format"] == "gpx"


def test_a_tcx_takes_its_timer_time_and_its_distance():
    # A five-minute stop: the laps' timer says 25 minutes, the clock 30.
    parsed = track_import.parse_track_activity(_tcx(timer_seconds=1500), "ride.tcx")

    assert parsed.sport_type == "cycling"
    assert (parsed.duration_seconds, parsed.duration_minutes) == (1500, 25)
    assert "heartrate" not in parsed.streams
    # 80 m every ten seconds, from the file's own distance.
    assert set(parsed.streams["velocity_smooth"]["data"]) == {8.0}
    assert parsed.streams["time"] == parsed.streams["velocity_time"]
    assert parsed.activity_name == "TCX Cycling 2026-09-01"
    assert parsed.source_metadata["file_format"] == "tcx"


def test_power_in_an_extension_is_read_and_sets_the_time_base():
    tcx = track_import.parse_track_activity(_tcx(watts=200), "ride.tcx")
    gpx = track_import.parse_track_activity(
        _gpx(power=180, heart_rate=None), "ride.gpx"
    )

    assert tcx.avg_power == 200
    assert tcx.streams["watts"]["data"][:2] == [200, 200]
    assert tcx.streams["time"]["data"][:3] == [0, 10, 20]
    assert gpx.avg_power == 180
    assert "heartrate" not in gpx.streams


def test_a_messy_file_keeps_what_it_can_read():
    """Real exports are not tidy: points without a time, a time that is not one,
    a value that is no number, a device speed and cadence on some points."""
    good = [
        f"<Trackpoint><Time>{_stamp(i)}</Time><AltitudeMeters>{'n/a' if i == 1 else 300}"
        "</AltitudeMeters><Cadence>85</Cadence>"
        "<Extensions><ns3:TPX><ns3:Speed>8.5</ns3:Speed></ns3:TPX></Extensions></Trackpoint>"
        for i in range(4)
    ]
    junk = [
        "<Trackpoint><Time>yesterday morning</Time></Trackpoint>",
        "<Trackpoint><AltitudeMeters>310</AltitudeMeters></Trackpoint>",
    ]
    tcx = (
        '<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2" '
        'xmlns:ns3="http://www.garmin.com/xmlschemas/ActivityExtension/v2">'
        '<Activities><Activity Sport="Biking"><Lap><Track>'
        f"{''.join(good[:2] + junk + good[2:])}</Track></Lap></Activity></Activities>"
        "</TrainingCenterDatabase>"
    ).encode()

    parsed = track_import.parse_track_activity(tcx, "indoor.tcx")

    assert parsed.source_metadata["points"] == 4
    # The device's own speed, not one worked out from positions it does not have.
    assert parsed.streams["velocity_smooth"]["data"] == [8.5, 8.5, 8.5, 8.5]
    assert parsed.streams["cadence"]["data"] == [85, 85, 85, 85]
    assert parsed.streams["cadence_time"]["data"] == [0, 10, 20, 30]
    # The point whose altitude was no number has none.
    assert parsed.streams["altitude_time"]["data"] == [0, 20, 30]
    assert "latlng" not in parsed.streams
    # No laps' timer: the clock from the first to the last point.
    assert parsed.duration_seconds == 30


def test_a_point_without_a_position_leaves_its_speed_to_its_neighbours():
    track = _gpx(minutes=1, heart_rate=None).replace(
        f'<trkpt lat="{47.99 + 3 * 0.0007:.6f}" lon="7.85">'.encode(),
        b'<trkpt lon="7.85">',
    )

    parsed = track_import.parse_track_activity(track, "gap.gpx")

    speeds = parsed.streams["velocity_smooth"]["data"]
    assert len(speeds) == 7
    assert all(7.7 < v < 7.9 for v in speeds)
    assert len(parsed.streams["latlng"]["data"]) == 6


@pytest.mark.parametrize(
    ("data", "name", "sport"),
    [
        (_gpx(sport="running"), "run.gpx", "running"),
        (_gpx(sport="Walk"), "walk.gpx", "walking"),
        (_gpx(sport="hiking"), "hike.gpx", "hiking"),
        (_gpx(sport="mountain_biking"), "mtb.gpx", "cycling"),
        (_tcx(sport="Running"), "run.tcx", "running"),
        # This is a cycling coach: what a file does not name is a ride.
        (_tcx(sport="Other"), "other.tcx", "cycling"),
    ],
)
def test_the_sport_comes_from_the_file(data, name, sport):
    assert track_import.parse_track_activity(data, name).sport_type == sport


def test_the_same_file_is_known_again_and_another_is_not():
    first = track_import.parse_track_activity(_gpx(), "a.gpx").source_id

    assert track_import.parse_track_activity(_gpx(), "copy.gpx").source_id == first
    assert (
        track_import.parse_track_activity(_gpx(minutes=31), "b.gpx").source_id != first
    )


@pytest.mark.parametrize(
    "data",
    [
        b"not xml at all",
        # An entity bomb: the standard library's parser would expand it.
        b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
        b'<!ENTITY lol2 "&lol;&lol;&lol;&lol;">]><gpx>&lol2;</gpx>',
    ],
)
def test_an_unreadable_file_is_refused(data):
    with pytest.raises(ValueError, match="could not be read as GPX or TCX"):
        track_import.parse_track_activity(data, "x.gpx")


def test_a_track_without_times_is_refused():
    with pytest.raises(ValueError, match="no track points with a time"):
        track_import.parse_track_activity(_gpx(with_time=False), "x.gpx")


# ---------------------------------------------------------------------------
# Uploading them
# ---------------------------------------------------------------------------


async def _bulk(client, headers, *files: tuple[str, bytes]) -> dict:
    response = await client.post(
        BULK,
        headers=headers,
        files=[("files", (n, d, "application/octet-stream")) for n, d in files],
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _stored_metric(email: str = "rider@example.com") -> models.RideMetric:
    async with TestSessionLocal() as db:
        user = await db.scalar(select(models.User).where(models.User.email == email))
        return await db.scalar(
            select(models.RideMetric).where(models.RideMetric.user_id == user.id)
        )


@pytest.mark.asyncio
async def test_a_gpx_with_heart_rate_is_booked_on_heart_rate(
    client: AsyncClient, auth_headers, mock_ai_service
):
    await client.put(
        "/api/v1/users/me", json={"maxHeartRate": 190}, headers=auth_headers
    )

    body = await _bulk(client, auth_headers, ("morning.gpx", _gpx()))

    [result] = body["files"]
    assert (result["status"], result["message"]) == (
        "imported",
        "Imported GPX activity",
    )
    assert result["loadSource"] == "heart_rate"
    assert (result["averageHeartRate"], result["durationMinutes"]) == (140, 30)
    metric = await _stored_metric()
    assert metric.tss_source == "heart_rate"
    assert metric.tss and metric.tss > 0


@pytest.mark.asyncio
async def test_without_a_maximum_heart_rate_the_load_rests_on_the_duration(
    client: AsyncClient, auth_headers, mock_ai_service
):
    # The file has heart rate; the athlete's profile has no maximum to read it by.
    body = await _bulk(client, auth_headers, ("morning.gpx", _gpx()))

    assert body["files"][0]["loadSource"] == "duration"


@pytest.mark.asyncio
async def test_a_tcx_without_heart_rate_is_booked_on_its_duration(
    client: AsyncClient, auth_headers, mock_ai_service
):
    await client.put(
        "/api/v1/users/me", json={"maxHeartRate": 190}, headers=auth_headers
    )

    body = await _bulk(client, auth_headers, ("ride.tcx", _tcx()))

    assert body["files"][0]["loadSource"] == "duration"
    assert body["files"][0]["message"] == "Imported TCX activity"
    assert (await _stored_metric()).tss_source == "duration"


@pytest.mark.asyncio
async def test_power_in_a_tcx_is_a_measured_load(
    client: AsyncClient, auth_headers, mock_ai_service
):
    await client.put("/api/v1/users/me", json={"currentFTP": 250}, headers=auth_headers)

    body = await _bulk(client, auth_headers, ("ride.tcx", _tcx(watts=200)))

    assert body["files"][0]["loadSource"] == "power"
    assert body["files"][0]["averagePower"] == 200


@pytest.mark.asyncio
async def test_the_single_upload_takes_a_tcx_and_names_the_source(
    client: AsyncClient, auth_headers, mock_ai_service
):
    response = await client.post(
        SINGLE,
        headers=auth_headers,
        files={"file": ("ride.TCX", _tcx(), "application/octet-stream")},
    )

    assert response.status_code == 200, response.text
    assert response.json()["load_source"] == "duration"
    assert response.json()["sport_type"] == "cycling"


@pytest.mark.asyncio
async def test_the_single_upload_still_refuses_other_files(
    client: AsyncClient, auth_headers
):
    response = await client.post(
        SINGLE,
        headers=auth_headers,
        files={"file": ("photo.jpg", b"\xff\xd8", "application/octet-stream")},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Only .fit, .gpx and .tcx files are accepted"


@pytest.mark.asyncio
async def test_a_gpx_uploaded_twice_is_booked_once(
    client: AsyncClient, auth_headers, mock_ai_service
):
    await _bulk(client, auth_headers, ("morning.gpx", _gpx()))

    again = await _bulk(client, auth_headers, ("morning (1).gpx", _gpx()))

    assert again["files"][0]["status"] == "skipped"
    assert again["files"][0]["message"] == "GPX activity was already imported"
    async with TestSessionLocal() as db:
        logs = (await db.scalars(select(models.WorkoutLog))).all()
    assert [log.notes for log in logs] == ["Imported from .gpx file: morning.gpx"]


# ---------------------------------------------------------------------------
# The effort the import used to invent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_import_reports_no_effort_of_its_own(
    client: AsyncClient, auth_headers, mock_ai_service
):
    # It wrote 5, the top of the 1-5 scale: a ride without power or heart rate
    # was priced as all-out ("rpe"), and shown as the hardest effort.
    await _bulk(client, auth_headers, ("ride.tcx", _tcx()))

    async with TestSessionLocal() as db:
        [log] = (await db.scalars(select(models.WorkoutLog))).all()
    assert log.perceived_effort == 0


@pytest.mark.asyncio
async def test_an_effort_the_athlete_logged_stays_and_prices_the_ride(
    client: AsyncClient, auth_headers, mock_ai_service
):
    logged = await client.post(
        "/api/v1/users/me/workouts/2026-09-01",
        headers=auth_headers,
        json={
            "feedback": {
                "actualDurationMinutes": 30,
                "perceivedEffort": 2,
                "notes": "Easy spin.",
                "completedAt": "2026-09-01T07:30:00Z",
            }
        },
    )
    assert logged.status_code == 200, logged.text

    body = await _bulk(client, auth_headers, ("ride.tcx", _tcx()))

    assert body["files"][0]["loadSource"] == "rpe"
    async with TestSessionLocal() as db:
        [log] = (await db.scalars(select(models.WorkoutLog))).all()
    assert log.perceived_effort == 2
