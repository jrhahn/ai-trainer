"""Intervals.icu API client and activity mapping helpers."""

from __future__ import annotations

import hashlib
from datetime import date
from typing import Any

import httpx

from services.activity_imports import ImportedActivity

INTERVALS_API_BASE = "https://intervals.icu/api/v1"
INTERVALS_STREAM_TYPES = (
    "time,watts,heartrate,cadence,velocity_smooth,altitude,latlng,distance"
)


class IntervalsAuthError(Exception):
    """Raised when Intervals.icu rejects stored credentials."""


class IntervalsAPIError(Exception):
    """Raised when Intervals.icu returns an unexpected API failure."""


class IntervalsDataUnavailable(Exception):
    """Streams/detail couldn't be fetched due to a transient error (429/5xx/network).

    Raised by :func:`fetch_activity_streams` / :func:`fetch_activity_detail` so
    import paths can retry rather than persist an activity with missing data. A
    genuine empty/absent response (permanent non-success) still returns ``{}``.
    Standalone (not an ``IntervalsAPIError``) so it does not trip the fatal
    import-level handler. See #352 (parallels #325).
    """


def _is_transient_intervals_status(status_code: int) -> bool:
    return status_code == 429 or status_code >= 500


def intervals_auth(api_key: str) -> httpx.BasicAuth:
    return httpx.BasicAuth("API_KEY", api_key)


def intervals_activity_id(activity_id: Any) -> int:
    """Return a stable signed-bigint-safe id for an Intervals.icu activity."""
    digest = hashlib.blake2b(str(activity_id).encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") & ((1 << 63) - 1)


async def fetch_recent_activities(
    api_key: str,
    athlete_id: str,
    *,
    oldest: date,
    newest: date,
) -> list[dict[str, Any]]:
    fields = ",".join(
        (
            "id",
            "name",
            "type",
            "start_date",
            "start_date_local",
            "moving_time",
            "elapsed_time",
            "average_watts",
            "icu_weighted_avg_watts",
            "icu_training_load",
            "icu_ctl",
            "icu_atl",
            "average_heartrate",
            "average_cadence",
            "distance",
            "start_latlng",
        )
    )
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{INTERVALS_API_BASE}/athlete/{athlete_id or '0'}/activities",
            params={
                "oldest": oldest.isoformat(),
                "newest": newest.isoformat(),
                "fields": fields,
            },
            auth=intervals_auth(api_key),
        )
    if resp.status_code in (401, 403):
        raise IntervalsAuthError("Intervals.icu credentials were rejected")
    if not resp.is_success:
        raise IntervalsAPIError(f"Intervals.icu list error {resp.status_code}")
    data = resp.json()
    return data if isinstance(data, list) else []


async def fetch_activity_detail(api_key: str, activity_id: Any) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{INTERVALS_API_BASE}/activity/{activity_id}",
                params={"intervals": "false"},
                auth=intervals_auth(api_key),
            )
    except httpx.RequestError as exc:
        raise IntervalsDataUnavailable(
            f"detail request failed for activity {activity_id}: {exc}"
        ) from exc
    if resp.status_code in (401, 403):
        raise IntervalsAuthError("Intervals.icu credentials were rejected")
    if _is_transient_intervals_status(resp.status_code):
        raise IntervalsDataUnavailable(
            f"transient Intervals.icu status {resp.status_code} for activity {activity_id}"
        )
    if not resp.is_success:
        return {}
    data = resp.json()
    return data if isinstance(data, dict) else {}


async def fetch_activity_streams(api_key: str, activity_id: Any) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{INTERVALS_API_BASE}/activity/{activity_id}/streams",
                params={"types": INTERVALS_STREAM_TYPES},
                auth=intervals_auth(api_key),
            )
    except httpx.RequestError as exc:
        raise IntervalsDataUnavailable(
            f"stream request failed for activity {activity_id}: {exc}"
        ) from exc
    if resp.status_code in (401, 403):
        raise IntervalsAuthError("Intervals.icu credentials were rejected")
    if _is_transient_intervals_status(resp.status_code):
        raise IntervalsDataUnavailable(
            f"transient Intervals.icu status {resp.status_code} for activity {activity_id}"
        )
    if not resp.is_success:
        return {}
    data = resp.json()
    return data if isinstance(data, (dict, list)) else {}


def sanitize_intervals_streams(streams: object) -> dict[str, dict[str, list]]:
    """Map common Intervals.icu stream payload shapes to Strava-style streams."""
    aliases = {
        "power": "watts",
        "watts": "watts",
        "heartrate": "heartrate",
        "heart_rate": "heartrate",
        "hr": "heartrate",
        "cadence": "cadence",
        "altitude": "altitude",
        "time": "time",
        "distance": "distance",
        "speed": "velocity_smooth",
        "velocity_smooth": "velocity_smooth",
    }
    collected: dict[str, list] = {}

    if isinstance(streams, dict):
        for raw_key, raw_value in streams.items():
            key = aliases.get(str(raw_key))
            if key is None:
                if raw_key == "latlng":
                    points = _latlng_points(raw_value)
                    if points:
                        collected["latlng"] = points
                continue
            values = raw_value.get("data") if isinstance(raw_value, dict) else raw_value
            numeric = _numeric_list(values)
            if numeric:
                collected[key] = numeric
    elif isinstance(streams, list):
        for sample in streams:
            if not isinstance(sample, dict):
                continue
            for raw_key, raw_value in sample.items():
                key = aliases.get(str(raw_key))
                if key is None:
                    continue
                if isinstance(raw_value, (int, float)):
                    collected.setdefault(key, []).append(float(raw_value))
            if "lat" in sample and "lng" in sample:
                lat = sample["lat"]
                lng = sample["lng"]
                if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
                    collected.setdefault("latlng", []).append([float(lat), float(lng)])

    cleaned: dict[str, dict[str, list]] = {}
    for key, data in collected.items():
        if key == "latlng":
            cleaned[key] = {"data": data}
        else:
            cleaned[key] = {"data": [float(v) for v in data]}
    return cleaned


def map_activity_to_imported_activity(
    activity: dict[str, Any],
    detail: dict[str, Any] | None,
    streams: dict[str, dict[str, list]],
) -> ImportedActivity | None:
    source = {**activity, **(detail or {})}
    raw_id = source.get("id")
    if raw_id is None:
        return None

    start_datetime = _first_str(
        source,
        "start_date_local",
        "start_date",
        "start_time",
        "date",
    )
    activity_date = start_datetime[:10] if start_datetime else ""
    if not activity_date:
        return None

    return ImportedActivity(
        source="intervals",
        external_activity_id=str(raw_id),
        name=_first_str(source, "name", "title"),
        start_datetime=start_datetime,
        activity_date=activity_date,
        sport_type=_first_str(source, "type", "sport", default="cycling") or "cycling",
        duration_seconds=_first_int(
            source, "moving_time", "elapsed_time", "duration", default=0
        ),
        start_lat=_start_latlng(source)[0],
        start_lng=_start_latlng(source)[1],
        streams=streams,
        summary_avg_power_w=_first_int(
            source, "average_watts", "icu_weighted_avg_watts"
        ),
        summary_normalized_power_w=_first_int(source, "icu_weighted_avg_watts"),
        summary_tss=_first_float(source, "icu_training_load", "training_load"),
        metadata={"intervals_activity_id": str(raw_id)},
        legacy_activity_id=intervals_activity_id(raw_id),
    )


def map_activity_to_ride_input(
    activity: dict[str, Any],
    detail: dict[str, Any] | None,
    streams: dict[str, dict[str, list]],
) -> dict[str, Any] | None:
    imported = map_activity_to_imported_activity(activity, detail, streams)
    return imported.to_ride_input() if imported is not None else None


def apply_summary_fallback(metric: dict[str, Any], ride: dict[str, Any]) -> None:
    """Fill fields available from Intervals.icu summaries when streams are missing."""
    if (
        metric.get("avg_power_w") is None
        and ride.get("_summary_avg_power_w") is not None
    ):
        metric["avg_power_w"] = ride["_summary_avg_power_w"]
    if (
        metric.get("normalized_power_w") is None
        and ride.get("_summary_np_w") is not None
    ):
        metric["normalized_power_w"] = ride["_summary_np_w"]
    if metric.get("tss") is None and ride.get("_summary_tss") is not None:
        metric["tss"] = ride["_summary_tss"]


def _first_str(
    source: dict[str, Any], *keys: str, default: str | None = None
) -> str | None:
    for key in keys:
        value = source.get(key)
        if isinstance(value, str) and value:
            return value
    return default


def _first_int(
    source: dict[str, Any], *keys: str, default: int | None = None
) -> int | None:
    for key in keys:
        value = source.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return round(value)
    return default


def _first_float(source: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = source.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def _numeric_list(values: object) -> list[float]:
    if not isinstance(values, list):
        return []
    return [float(v) for v in values if isinstance(v, (int, float))]


def _latlng_points(values: object) -> list[list[float]]:
    raw = values.get("data") if isinstance(values, dict) else values
    if not isinstance(raw, list):
        return []
    return [
        [float(point[0]), float(point[1])]
        for point in raw
        if isinstance(point, (list, tuple))
        and len(point) >= 2
        and isinstance(point[0], (int, float))
        and isinstance(point[1], (int, float))
    ]


def _start_latlng(source: dict[str, Any]) -> tuple[float | None, float | None]:
    start_latlng = source.get("start_latlng")
    if (
        isinstance(start_latlng, (list, tuple))
        and len(start_latlng) >= 2
        and isinstance(start_latlng[0], (int, float))
        and isinstance(start_latlng[1], (int, float))
    ):
        return float(start_latlng[0]), float(start_latlng[1])
    lat = source.get("start_lat") or source.get("lat")
    lng = source.get("start_lng") or source.get("lng")
    if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
        return float(lat), float(lng)
    return None, None
