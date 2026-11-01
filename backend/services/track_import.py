"""Importing a .gpx or .tcx file as a ride (ai-trainer-ops#311).

A GPX or TCX export carries a track, and maybe heart rate, cadence or power,
but not the summary a device writes into a .fit. Since #48 the upload refused
both, so a rider with nothing else had no way in. The owner, 2026-10-26:
estimate the load where it can be done, and say so.

The file becomes the ``ParsedFitActivity`` a .fit becomes, and the load chain
prices it as it prices any ride: from power where an extension carries it, from
heart rate (hrTSS) where the file has that, from pace for a run, and from the
duration otherwise. The stored ``tss_source`` says which, and the upload says
it to the athlete.

The files come from the athlete's browser, so defusedxml parses them: the
standard library's parser would expand an entity bomb.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import ParseError, fromstring

from services import fit_import
from services.fit_import import ParsedFitActivity, upload_source_id

TRACK_FORMATS = (".gpx", ".tcx")

# What the two formats call a sport, in the words a .fit uses. Anything else is
# a ride: this is a cycling coach, and the result names the sport it booked.
_RUN_WORDS = ("run",)
_WALK_WORDS = ("walk",)
_HIKE_WORDS = ("hik",)
_DEFAULT_SPORT = "cycling"

# Speeds a GPS track gives point by point jump with every fix; the mean of a
# few neighbours is what a device's own "smoothed" speed is too.
_SPEED_WINDOW = 5

_EARTH_RADIUS_M = 6_371_000

# A whole GPX or TCX is parsed into a tree: about 25 times its size in memory,
# seconds of CPU holding the GIL. A long ride's track is well under 5 MB; ten
# uploads of 10 MB at once froze the single worker for everyone (review of
# 2026-11-01, ai-trainer-ops#338).
TRACK_MAX_BYTES = 5 * 1024 * 1024
TOO_LARGE = "The file is larger than 5 MB. Export the ride as .fit instead."

UNREADABLE = "The file could not be read as GPX or TCX."
NO_TRACK = "The file has no track points with a time."


@dataclass
class _Point:
    time: datetime
    lat: float | None = None
    lng: float | None = None
    altitude: float | None = None
    distance: float | None = None
    speed: float | None = None
    heart_rate: int | None = None
    cadence: int | None = None
    power: int | None = None


def _text(element: Any, path: str) -> str | None:
    found = element.find(path)
    if found is None or found.text is None:
        return None
    return found.text.strip() or None


def _float(value: str | None) -> float | None:
    try:
        number = float(value) if value is not None else None
    except ValueError:
        return None
    return number if number is not None and math.isfinite(number) else None


def _positive_int(value: str | None) -> int | None:
    number = _float(value)
    return round(number) if number is not None and number > 0 else None


def _time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _gpx_points(root: Any) -> list[_Point]:
    points = []
    for point in root.iterfind(".//{*}trkpt"):
        time = _time(_text(point, "{*}time"))
        if time is None:
            continue
        points.append(
            _Point(
                time=time,
                lat=_float(point.get("lat")),
                lng=_float(point.get("lon")),
                altitude=_float(_text(point, "{*}ele")),
                # Garmin's TrackPointExtension, and the plain tags other apps write.
                heart_rate=_positive_int(_text(point, ".//{*}hr")),
                cadence=_positive_int(_text(point, ".//{*}cad")),
                power=_positive_int(_text(point, ".//{*}power")),
            )
        )
    return points


def _tcx_points(root: Any) -> list[_Point]:
    points = []
    for point in root.iterfind(".//{*}Trackpoint"):
        time = _time(_text(point, "{*}Time"))
        if time is None:
            continue
        points.append(
            _Point(
                time=time,
                lat=_float(_text(point, "{*}Position/{*}LatitudeDegrees")),
                lng=_float(_text(point, "{*}Position/{*}LongitudeDegrees")),
                altitude=_float(_text(point, "{*}AltitudeMeters")),
                distance=_float(_text(point, "{*}DistanceMeters")),
                speed=_float(_text(point, ".//{*}Speed")),
                heart_rate=_positive_int(_text(point, "{*}HeartRateBpm/{*}Value")),
                cadence=_positive_int(_text(point, "{*}Cadence")),
                power=_positive_int(_text(point, ".//{*}Watts")),
            )
        )
    return points


def _sport(word: str | None) -> str:
    lowered = (word or "").lower()
    if any(part in lowered for part in _RUN_WORDS):
        return "running"
    if any(part in lowered for part in _WALK_WORDS):
        return "walking"
    if any(part in lowered for part in _HIKE_WORDS):
        return "hiking"
    return _DEFAULT_SPORT


def _metres_between(a: _Point, b: _Point) -> float | None:
    if a.lat is None or a.lng is None or b.lat is None or b.lng is None:
        return None
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    dlat = lat2 - lat1
    dlng = math.radians(b.lng - a.lng)
    h = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(min(1.0, h)))


def _speeds(points: list[_Point]) -> list[float | None]:
    """Metres per second at each point: the file's own, else from the track."""
    if any(point.speed is not None for point in points):
        return [point.speed for point in points]
    raw: list[float | None] = [None]
    for before, point in zip(points, points[1:]):
        seconds = (point.time - before.time).total_seconds()
        if before.distance is not None and point.distance is not None:
            metres: float | None = point.distance - before.distance
        else:
            metres = _metres_between(before, point)
        raw.append(metres / seconds if metres is not None and seconds > 0 else None)
    smoothed: list[float | None] = []
    half = _SPEED_WINDOW // 2
    for i in range(len(raw)):
        window = [v for v in raw[max(0, i - half) : i + half + 1] if v is not None]
        smoothed.append(sum(window) / len(window) if window else None)
    return smoothed


async def parse_track_off_loop(raw: bytes, filename: str) -> ParsedFitActivity:
    """Parse off the event loop, one upload at a time, as a .fit is parsed
    (``fit_import.parse_fit_off_loop``), and never a file over the cap."""
    if len(raw) > TRACK_MAX_BYTES:
        raise ValueError(TOO_LARGE)
    async with fit_import.FIT_PARSE_SLOTS:
        return await asyncio.to_thread(parse_track_activity, raw, filename)


def parse_track_activity(raw: bytes, filename: str) -> ParsedFitActivity:
    """A .gpx or .tcx upload as the activity a .fit upload is parsed into."""
    try:
        root = fromstring(raw)
    except (ParseError, DefusedXmlException) as exc:
        raise ValueError(UNREADABLE) from exc

    is_tcx = filename.lower().endswith(".tcx")
    points = sorted(
        _tcx_points(root) if is_tcx else _gpx_points(root), key=lambda p: p.time
    )
    if not points:
        raise ValueError(NO_TRACK)

    if is_tcx:
        activity = root.find(".//{*}Activity")
        sport = _sport(activity.get("Sport") if activity is not None else None)
        name = None
        # The timer's time, which leaves out the pauses, as a device's summary does.
        timer = sum(
            _float(lap.findtext("{*}TotalTimeSeconds")) or 0.0
            for lap in root.iterfind(".//{*}Lap")
        )
    else:
        sport = _sport(_text(root, ".//{*}trk/{*}type"))
        name = _text(root, ".//{*}trk/{*}name") or _text(root, "{*}metadata/{*}name")
        timer = 0.0

    start = points[0].time
    elapsed = int((points[-1].time - start).total_seconds())
    duration_seconds = max(1, round(timer) if timer > 0 else elapsed)
    completed_at = start.isoformat()
    ride_date = completed_at[:10]

    def seconds(point: _Point) -> int:
        return max(0, int((point.time - start).total_seconds()))

    streams: dict[str, dict[str, list[Any]]] = {}
    watts = [(seconds(p), p.power) for p in points if p.power]
    speeds = [
        (seconds(p), round(v, 3))
        for p, v in zip(points, _speeds(points))
        if v is not None
    ]
    heart = [(seconds(p), p.heart_rate) for p in points if p.heart_rate]
    cadence = [(seconds(p), p.cadence) for p in points if p.cadence]
    altitude = [
        (seconds(p), round(p.altitude, 2)) for p in points if p.altitude is not None
    ]
    latlng = [
        [round(p.lat, 6), round(p.lng, 6)]
        for p in points
        if p.lat is not None and p.lng is not None
    ]
    # The same time base a .fit gets: the power samples', else the speed samples'.
    if watts:
        streams["watts"] = {"data": [w for _, w in watts]}
        streams["time"] = {"data": [t for t, _ in watts]}
    elif speeds:
        streams["time"] = {"data": [t for t, _ in speeds]}
    if heart:
        streams["heartrate"] = {"data": [h for _, h in heart]}
        streams["heartrate_time"] = {"data": [t for t, _ in heart]}
    if cadence:
        streams["cadence"] = {"data": [c for _, c in cadence]}
        streams["cadence_time"] = {"data": [t for t, _ in cadence]}
    if altitude:
        streams["altitude"] = {"data": [a for _, a in altitude]}
        streams["altitude_time"] = {"data": [t for t, _ in altitude]}
    if speeds:
        streams["velocity_smooth"] = {"data": [v for _, v in speeds]}
        streams["velocity_time"] = {"data": [t for t, _ in speeds]}
    if latlng:
        streams["latlng"] = {"data": latlng}

    file_format = "tcx" if is_tcx else "gpx"
    metadata = {
        "file_format": file_format,
        "activity_start_time": completed_at,
        "sport_type": sport,
        "duration_seconds": duration_seconds,
        "points": len(points),
    }
    return ParsedFitActivity(
        source_id=upload_source_id(raw, metadata),
        source_metadata=metadata,
        sport_type=sport,
        duration_seconds=duration_seconds,
        duration_minutes=max(1, round(duration_seconds / 60)),
        ride_date=ride_date,
        completed_at=completed_at,
        activity_name=name or f"{file_format.upper()} {sport.title()} {ride_date}",
        avg_power=round(sum(w for _, w in watts) / len(watts)) if watts else None,
        avg_hr=round(sum(h for _, h in heart) / len(heart)) if heart else None,
        start_lat=latlng[0][0] if latlng else None,
        start_lng=latlng[0][1] if latlng else None,
        streams=streams,
    )
