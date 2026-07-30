"""Athlete training location: clustering, resolution, and the user-set override (#495)."""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
from services import home_location
from tests.conftest import TestSessionLocal


@pytest_asyncio.fixture
async def db() -> AsyncSession:
    """Yield a fresh session that is rolled back after each test."""
    async with TestSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@pytest_asyncio.fixture
async def user(db: AsyncSession) -> models.User:
    return await crud.create_user(
        db, email="home-location@example.com", name="Rider", hashed_password="x"
    )


# ---------------------------------------------------------------------------
# Pure clustering
# ---------------------------------------------------------------------------


def test_cluster_ride_starts_returns_none_without_points():
    assert home_location.cluster_ride_starts([]) is None


def test_cluster_ride_starts_ignores_null_island_and_invalid_points():
    assert home_location.cluster_ride_starts([(0.0, 0.0), (None, 5.0), (91.0, 5.0)]) is None


def test_cluster_ride_starts_picks_dominant_cluster_not_latest_ride():
    """The whole point of clustering: one holiday ride must not move the base.

    The newest point (Mallorca) is a single away ride; the athlete's real base is
    the tight Freiburg cluster, and the centroid must land there.
    """
    points = [
        (39.57, 2.65),  # newest — holiday ride, far away
        (47.99, 7.85),
        (48.01, 7.84),
        (47.98, 7.86),
        (48.00, 7.83),
    ]

    cluster = home_location.cluster_ride_starts(points)

    assert cluster is not None
    assert cluster.ride_count == 4
    assert cluster.total_count == 5
    assert 47.9 < cluster.latitude < 48.1
    assert 7.8 < cluster.longitude < 7.9


def test_cluster_confidence_grows_with_sample_size():
    """Same 100 % share, more rides behind it — the belief must get stronger."""
    thin = home_location.cluster_ride_starts([(48.0, 7.85)] * 4)
    thick = home_location.cluster_ride_starts([(48.0, 7.85)] * 40)

    assert thin is not None and thick is not None
    assert thin.share == thick.share == 1.0
    assert thick.confidence > thin.confidence
    assert thick.confidence <= 0.95


def test_haversine_km_matches_known_distance():
    # Freiburg → Berlin is roughly 640 km.
    distance = home_location.haversine_km(47.99, 7.85, 52.52, 13.40)
    assert 600 < distance < 700


# ---------------------------------------------------------------------------
# Statement extraction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message,expected",
    [
        ("I mostly train near Freiburg now", "Freiburg"),
        ("I now mainly ride around Innsbruck", "Innsbruck"),
        ("I just moved to Girona", "Girona"),
        ("My rides now start in Boulder", "Boulder"),
        ("My new home base is near Sion", "Sion"),
        ("Ich trainiere jetzt meistens in Freiburg", "Freiburg"),
        ("Ich bin nach Innsbruck gezogen", "Innsbruck"),
    ],
)
def test_extract_home_location_statement_recognises_relocations(message, expected):
    assert home_location.extract_home_location_statement(message) == expected


@pytest.mark.parametrize(
    "message",
    [
        "",
        "That climb in Italy was brutal",
        "I rode to Freiburg yesterday",
        "Should I train tomorrow?",
        "The weather in Girona looks great this week",
    ],
)
def test_extract_home_location_statement_ignores_passing_mentions(message):
    """A place named in passing must never move the athlete's training base."""
    assert home_location.extract_home_location_statement(message) is None


# ---------------------------------------------------------------------------
# Persistence: the user-set override must survive inference
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inferred_write_does_not_clobber_user_set_location(db: AsyncSession, user: models.User):
    """The stale-snapshot clobber class (#342/#345/#346), applied to location."""
    await crud.upsert_athlete_home_location(
        db,
        user.id,
        latitude=47.99,
        longitude=7.85,
        label="Freiburg",
        source="user_set",
        confidence=1.0,
    )

    result = await crud.upsert_athlete_home_location(
        db,
        user.id,
        latitude=52.52,
        longitude=13.40,
        source="inferred",
        confidence=0.9,
        ride_count=30,
    )

    assert result is not None
    assert result.source == "user_set"
    assert result.label == "Freiburg"
    assert result.latitude == pytest.approx(47.99)


@pytest.mark.asyncio
async def test_user_set_write_overrides_inferred_location(db: AsyncSession, user: models.User):
    await crud.upsert_athlete_home_location(
        db,
        user.id,
        latitude=52.52,
        longitude=13.40,
        source="inferred",
        confidence=0.8,
        ride_count=20,
    )

    result = await crud.upsert_athlete_home_location(
        db,
        user.id,
        latitude=47.99,
        longitude=7.85,
        label="Freiburg",
        source="user_set",
        confidence=1.0,
    )

    assert result is not None
    assert result.source == "user_set"
    assert result.latitude == pytest.approx(47.99)


@pytest.mark.asyncio
async def test_inferred_refresh_keeps_existing_label(db: AsyncSession, user: models.User):
    """Re-inference may move the centroid but must not blank a known place name."""
    await crud.upsert_athlete_home_location(
        db,
        user.id,
        latitude=47.99,
        longitude=7.85,
        label="Freiburg",
        source="inferred",
        confidence=0.5,
        ride_count=5,
    )

    result = await crud.upsert_athlete_home_location(
        db,
        user.id,
        latitude=48.01,
        longitude=7.86,
        source="inferred",
        confidence=0.7,
        ride_count=12,
    )

    assert result is not None
    assert result.label == "Freiburg"
    assert result.ride_count == 12


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "latitude,longitude",
    [(200.0, 7.85), (47.99, 400.0), (-91.0, 0.0), (None, 7.85), (47.99, None)],
)
async def test_upsert_rejects_unusable_coordinates(
    db: AsyncSession, user: models.User, latitude, longitude
):
    assert (
        await crud.upsert_athlete_home_location(
            db, user.id, latitude=latitude, longitude=longitude
        )
        is None
    )
    assert await crud.get_athlete_home_location(db, user.id) is None


# ---------------------------------------------------------------------------
# Inference + resolution against the database
# ---------------------------------------------------------------------------


def _ride(user_id: str, activity_id: int, date: str, lat, lng) -> models.RideMetric:
    return models.RideMetric(
        user_id=user_id,
        strava_activity_id=activity_id,
        activity_date=date,
        start_lat=lat,
        start_lng=lng,
    )


@pytest.mark.asyncio
async def test_infer_home_location_needs_minimum_history(db: AsyncSession, user: models.User):
    db.add(_ride(user.id, 1, "2026-07-01", 47.99, 7.85))
    db.add(_ride(user.id, 2, "2026-07-02", 48.00, 7.84))
    await db.flush()

    assert await home_location.infer_home_location(db, user.id) is None


@pytest.mark.asyncio
async def test_infer_home_location_persists_cluster_centroid(db: AsyncSession, user: models.User):
    for idx, (date, lat, lng) in enumerate(
        [
            ("2026-07-01", 47.99, 7.85),
            ("2026-07-02", 48.00, 7.84),
            ("2026-07-03", 48.01, 7.86),
            ("2026-07-04", 39.57, 2.65),
        ]
    ):
        db.add(_ride(user.id, 100 + idx, date, lat, lng))
    await db.flush()

    row = await home_location.infer_home_location(db, user.id)

    assert row is not None
    assert row.source == "inferred"
    assert row.ride_count == 3
    assert 47.9 < row.latitude < 48.1


@pytest.mark.asyncio
async def test_resolve_training_location_prefers_persisted_attribute(
    db: AsyncSession, user: models.User
):
    db.add(_ride(user.id, 200, "2026-07-20", 39.57, 2.65))
    await db.flush()
    await crud.upsert_athlete_home_location(
        db,
        user.id,
        latitude=47.99,
        longitude=7.85,
        label="Freiburg",
        source="user_set",
        confidence=1.0,
    )

    resolved = await home_location.resolve_training_location(db, user.id)

    assert resolved is not None
    assert resolved.source == "user_set"
    assert resolved.latitude == pytest.approx(47.99)


@pytest.mark.asyncio
async def test_resolve_training_location_falls_back_to_latest_ride(
    db: AsyncSession, user: models.User
):
    """No stored attribute: nothing regresses for athletes never through inference."""
    db.add(_ride(user.id, 300, "2026-07-20", 52.52, 13.40))
    await db.flush()

    resolved = await home_location.resolve_training_location(db, user.id)

    assert resolved is not None
    assert resolved.source == "latest_ride"
    assert resolved.latitude == pytest.approx(52.52)


@pytest.mark.asyncio
async def test_resolve_training_location_returns_none_without_any_gps(
    db: AsyncSession, user: models.User
):
    assert await home_location.resolve_training_location(db, user.id) is None


# ---------------------------------------------------------------------------
# Geocoding
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, payload, is_success=True):
        self._payload = payload
        self.is_success = is_success

    def json(self):
        return self._payload


def _patch_httpx(monkeypatch, resp, calls=None):
    """Stub httpx.AsyncClient so geocoding is exercised without a network call."""

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            if calls is not None:
                calls.append((url, params))
            if isinstance(resp, Exception):
                raise resp
            return resp

    monkeypatch.setattr(home_location.httpx, "AsyncClient", _FakeClient)


@pytest.mark.asyncio
async def test_geocode_place_returns_coordinates_and_canonical_name(monkeypatch):
    calls: list = []
    _patch_httpx(
        monkeypatch,
        _FakeResp(
            {
                "results": [
                    {"latitude": 47.996, "longitude": 7.849, "name": "Freiburg im Breisgau"}
                ]
            }
        ),
        calls,
    )

    result = await home_location.geocode_place("Freiburg")

    assert result == (47.996, 7.849, "Freiburg im Breisgau")
    assert calls[0][0] == home_location.GEOCODING_URL
    assert calls[0][1]["name"] == "Freiburg"
    # One result is all we need; asking for more would just cost bandwidth.
    assert calls[0][1]["count"] == 1


@pytest.mark.asyncio
async def test_geocode_place_falls_back_to_the_query_when_no_name_is_returned(
    monkeypatch,
):
    _patch_httpx(
        monkeypatch, _FakeResp({"results": [{"latitude": 1.0, "longitude": 2.0}]})
    )

    assert await home_location.geocode_place("  Nowhere  ") == (1.0, 2.0, "Nowhere")


@pytest.mark.asyncio
async def test_geocode_place_returns_none_for_an_empty_query(monkeypatch):
    def _explode(*a, **k):
        raise AssertionError("must not call the geocoder for an empty name")

    monkeypatch.setattr(home_location.httpx, "AsyncClient", _explode)

    assert await home_location.geocode_place("") is None
    assert await home_location.geocode_place("   ") is None
    assert await home_location.geocode_place(None) is None


@pytest.mark.asyncio
async def test_geocode_place_returns_none_on_an_error_response(monkeypatch):
    _patch_httpx(monkeypatch, _FakeResp({}, is_success=False))
    assert await home_location.geocode_place("Freiburg") is None


@pytest.mark.asyncio
async def test_geocode_place_returns_none_when_the_lookup_raises(monkeypatch):
    """A geocoding hiccup must never break the chat turn that triggered it."""
    _patch_httpx(monkeypatch, RuntimeError("DNS exploded"))
    assert await home_location.geocode_place("Freiburg") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"results": []},
        {"results": None},
        "not a dict",
        {"results": [{"longitude": 7.85}]},
        {"results": [{"latitude": "north", "longitude": 7.85}]},
    ],
)
async def test_geocode_place_returns_none_for_unusable_payloads(monkeypatch, payload):
    _patch_httpx(monkeypatch, _FakeResp(payload))
    assert await home_location.geocode_place("Freiburg") is None


# ---------------------------------------------------------------------------
# Chat capture
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_capture_home_location_from_message_stores_user_set(
    db: AsyncSession, user: models.User, monkeypatch
):
    async def fake_geocode(name):
        assert name == "Freiburg"
        return 47.99, 7.85, "Freiburg im Breisgau"

    monkeypatch.setattr(home_location, "geocode_place", fake_geocode)

    row = await home_location.capture_home_location_from_message(
        db, user.id, "I mostly train near Freiburg now"
    )

    assert row is not None
    assert row.source == "user_set"
    assert row.label == "Freiburg im Breisgau"
    assert row.confidence == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_capture_home_location_noop_when_geocoding_fails(
    db: AsyncSession, user: models.User, monkeypatch
):
    async def fake_geocode(name):
        return None

    monkeypatch.setattr(home_location, "geocode_place", fake_geocode)

    row = await home_location.capture_home_location_from_message(
        db, user.id, "I mostly train near Atlantis now"
    )

    assert row is None
    assert await crud.get_athlete_home_location(db, user.id) is None


@pytest.mark.asyncio
async def test_capture_home_location_noop_without_a_statement(db: AsyncSession, user: models.User):
    row = await home_location.capture_home_location_from_message(
        db, user.id, "How was my ride yesterday?"
    )
    assert row is None
