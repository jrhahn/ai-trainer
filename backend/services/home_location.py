"""The athlete's usual training location — inferred, persisted, overridable (#495).

Weather only helps if it is the weather *where the athlete actually trains*. This
module owns that one question and has three parts:

* :func:`cluster_ride_starts` — a pure, dependency-free spatial clustering of ride
  start points. The dominant cluster's centroid is the athlete's training base,
  which is robust to a holiday week or a one-off away ride in a way the previous
  "latest ride with GPS" lookup was not.
* :func:`infer_home_location` — persists that centroid as an ``inferred``
  attribute, and :func:`resolve_training_location` reads the attribute back with a
  latest-ride fallback so nothing regresses for athletes with no stored row yet.
* :func:`capture_home_location_from_message` — the athlete's own override. When
  they tell the coach "I mostly train near Freiburg now", the place is geocoded
  and stored as ``user_set``, which
  :func:`crud.upsert_athlete_home_location` then protects from every later
  inference pass (the stale-snapshot clobber class, #342/#345/#346).
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models

logger = logging.getLogger(__name__)

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"

# Two ride starts belong to the same training base when they are within this
# radius. Wide enough to absorb different trailheads and city-edge starts around
# one home, tight enough that a genuinely different region forms its own cluster.
CLUSTER_RADIUS_KM = 30.0

# How many recent ride starts the inference pass considers.
RIDE_START_SAMPLE = 120

# Below this many clustered rides there is not enough history to claim a base.
MIN_CLUSTER_RIDES = 3

EARTH_RADIUS_KM = 6371.0


@dataclass(slots=True)
class LocationCluster:
    """The dominant training base found in a set of ride starts."""

    latitude: float
    longitude: float
    ride_count: int
    total_count: int

    @property
    def share(self) -> float:
        """Fraction of sampled rides that started inside the cluster."""
        return self.ride_count / self.total_count if self.total_count else 0.0

    @property
    def confidence(self) -> float:
        """How much to trust this as *the* training base.

        Grows with both the share of rides in the cluster and the sample size, so
        "8 of 10 rides" is trusted less than "80 of 100" and neither is ever
        asserted as certain.
        """
        sample_weight = min(1.0, self.ride_count / 20.0)
        return round(min(0.95, self.share * (0.55 + 0.45 * sample_weight)), 2)


@dataclass(slots=True)
class TrainingLocation:
    """Resolved coordinates for a weather lookup, plus where they came from."""

    latitude: float
    longitude: float
    label: str = ""
    # "user_set" | "inferred" | "latest_ride"
    source: str = "inferred"
    confidence: float = 0.0


def haversine_km(
    lat_a: float, lng_a: float, lat_b: float, lng_b: float
) -> float:
    """Great-circle distance in kilometres between two coordinates."""
    lat_a_rad, lat_b_rad = math.radians(lat_a), math.radians(lat_b)
    d_lat = lat_b_rad - lat_a_rad
    d_lng = math.radians(lng_b - lng_a)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(lat_a_rad) * math.cos(lat_b_rad) * math.sin(d_lng / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def cluster_ride_starts(
    points: list[tuple[float, float]],
    *,
    radius_km: float = CLUSTER_RADIUS_KM,
) -> LocationCluster | None:
    """Return the dominant cluster of ride start points, or ``None``.

    Each point seeds a candidate cluster of everything within ``radius_km``; the
    largest wins (ties broken by the earliest — i.e. most recent — point, since
    callers pass newest-first). The returned coordinate is the cluster's centroid,
    not one of the rides, so it sits in the middle of where the athlete rides.
    """
    usable = [
        (float(lat), float(lng))
        for lat, lng in points
        if lat is not None
        and lng is not None
        and -90.0 <= float(lat) <= 90.0
        and -180.0 <= float(lng) <= 180.0
        and not (float(lat) == 0.0 and float(lng) == 0.0)
    ]
    if not usable:
        return None

    best: list[tuple[float, float]] = []
    for centre_lat, centre_lng in usable:
        members = [
            (lat, lng)
            for lat, lng in usable
            if haversine_km(centre_lat, centre_lng, lat, lng) <= radius_km
        ]
        if len(members) > len(best):
            best = members
    if not best:
        return None

    return LocationCluster(
        latitude=round(sum(lat for lat, _ in best) / len(best), 4),
        longitude=round(sum(lng for _, lng in best) / len(best), 4),
        ride_count=len(best),
        total_count=len(usable),
    )


async def infer_home_location(
    db: AsyncSession,
    user_id: str,
    *,
    now: datetime | None = None,
) -> models.AthleteHomeLocation | None:
    """Cluster recent ride starts and persist the result as ``inferred``.

    Best-effort: returns ``None`` when there is not enough GPS history to claim a
    base. A ``user_set`` location is left untouched by the write gate in
    :func:`crud.upsert_athlete_home_location`, so this is safe to run on every
    sync.
    """
    points = await crud.get_recent_ride_start_locations(
        db, user_id, limit=RIDE_START_SAMPLE
    )
    cluster = cluster_ride_starts(points)
    if cluster is None or cluster.ride_count < MIN_CLUSTER_RIDES:
        return None
    return await crud.upsert_athlete_home_location(
        db,
        user_id,
        latitude=cluster.latitude,
        longitude=cluster.longitude,
        source="inferred",
        confidence=cluster.confidence,
        ride_count=cluster.ride_count,
        now=now,
    )


async def resolve_training_location(
    db: AsyncSession,
    user_id: str,
) -> TrainingLocation | None:
    """Return the coordinates every weather lookup for this athlete should use.

    Prefers the persisted attribute (athlete override first, then the inferred
    cluster) and falls back to the latest ride with GPS so athletes who have not
    been through an inference pass keep working exactly as before.
    """
    stored = await crud.get_athlete_home_location(db, user_id)
    if stored is not None:
        return TrainingLocation(
            latitude=stored.latitude,
            longitude=stored.longitude,
            label=stored.label,
            source=stored.source,
            confidence=stored.confidence,
        )

    latest = await crud.get_latest_ride_metric_with_location(db, user_id)
    if latest is None or latest.start_lat is None or latest.start_lng is None:
        return None
    return TrainingLocation(
        latitude=float(latest.start_lat),
        longitude=float(latest.start_lng),
        source="latest_ride",
    )


# ---------------------------------------------------------------------------
# Athlete override from chat
# ---------------------------------------------------------------------------

# High-confidence phrasing for "this is where I train now". Deliberately narrow:
# an ordinary mention of a place ("that climb in Italy was brutal") must never
# move the athlete's training base. Each pattern captures the place name.
_LOCATION_PATTERNS = (
    r"\bi\s+(?:now\s+)?(?:mostly|mainly|usually|generally)\s+(?:train|ride|cycle)\s+"
    r"(?:in|near|around|out\s+of|from)\s+(?P<place>[^.,;!?]{2,60})",
    r"\bi\s+(?:just\s+)?moved\s+to\s+(?P<place>[^.,;!?]{2,60})",
    r"\bmy\s+(?:rides?|training)\s+(?:now\s+)?starts?\s+"
    r"(?:in|near|around|from)\s+(?P<place>[^.,;!?]{2,60})",
    r"\bmy\s+(?:new\s+)?(?:home|training)\s+(?:base|location)\s+is\s+"
    r"(?:in|near|around\s+)?(?P<place>[^.,;!?]{2,60})",
    r"\bich\s+trainiere\s+(?:jetzt\s+)?(?:meistens|meist|hauptsächlich|normalerweise)\s+"
    r"(?:in|bei|um|rund\s+um)\s+(?P<place>[^.,;!?]{2,60})",
    r"\bich\s+bin\s+nach\s+(?P<place>[^.,;!?]{2,60})\s+gezogen",
    r"\bich\s+wohne\s+(?:jetzt\s+)?in\s+(?P<place>[^.,;!?]{2,60})",
)

# Trailing filler the capture group inevitably picks up ("...near Freiburg now").
_PLACE_TRAILING = re.compile(
    r"\s+(?:now|nowadays|these\s+days|from\s+now\s+on|jetzt|inzwischen|mittlerweile)$"
)


def extract_home_location_statement(text: str) -> str | None:
    """Return the place name the athlete named as their training base, or ``None``.

    Deterministic and high-confidence by design — the same approach as the
    availability-constraint extractor (:mod:`services.availability`): recognise a
    narrow set of explicit phrasings rather than asking an LLM to guess whether a
    place mentioned in passing was a relocation.
    """
    normalized = " ".join((text or "").split())
    if not normalized:
        return None
    for pattern in _LOCATION_PATTERNS:
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if match is None:
            continue
        place = _PLACE_TRAILING.sub("", match.group("place").strip()).strip(" '\"")
        if len(place) >= 2:
            return place
    return None


async def geocode_place(name: str) -> tuple[float, float, str] | None:
    """Resolve a place name to ``(latitude, longitude, canonical_name)``.

    Uses Open-Meteo's geocoding API — same provider, still no API key. Returns
    ``None`` on any failure so a chat turn is never broken by a geocoding hiccup.
    """
    cleaned = (name or "").strip()
    if not cleaned:
        return None
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                GEOCODING_URL,
                params={"name": cleaned, "count": 1, "language": "en", "format": "json"},
            )
        if not resp.is_success:
            return None
        payload = resp.json()
    except Exception:
        logger.info("Geocoding lookup failed for %r", cleaned, exc_info=True)
        return None

    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list) or not results:
        return None
    first = results[0]
    latitude = first.get("latitude")
    longitude = first.get("longitude")
    if not isinstance(latitude, (int, float)) or not isinstance(longitude, (int, float)):
        return None
    label = first.get("name") if isinstance(first.get("name"), str) else cleaned
    return float(latitude), float(longitude), label


async def capture_home_location_from_message(
    db: AsyncSession,
    user_id: str,
    message: str,
    *,
    now: datetime | None = None,
) -> models.AthleteHomeLocation | None:
    """Apply an athlete-stated training location from a coach-chat message.

    Returns the stored row when the message named a resolvable place, else
    ``None``. The write is ``user_set``, which pins it against later inference.
    """
    place = extract_home_location_statement(message)
    if place is None:
        return None
    geocoded = await geocode_place(place)
    if geocoded is None:
        return None
    latitude, longitude, label = geocoded
    return await crud.upsert_athlete_home_location(
        db,
        user_id,
        latitude=latitude,
        longitude=longitude,
        label=label,
        source="user_set",
        confidence=1.0,
        now=now,
    )
