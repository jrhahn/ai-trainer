import pytest

from services import weather_service


def test_coordinates_from_streams_uses_midpoint_gps_sample():
    lat, lng = weather_service.coordinates_from_streams(
        {
            "time": {"data": [0, 100, 200, 300, 400]},
            "latlng": {
                "data": [
                    [0, 0],
                    [52.51, 13.39],
                    [52.52, 13.405],
                    [52.53, 13.41],
                    [52.54, 13.42],
                ]
            },
        }
    )

    assert lat == 52.52
    assert lng == 13.405


def test_midpoint_datetime_from_values_crosses_date_boundary():
    assert (
        weather_service._midpoint_datetime_from_values("2026-05-05T23:30:00", 7200)
        == "2026-05-06T00:30:00"
    )


@pytest.mark.asyncio
async def test_enrich_activity_weather_uses_midpoint_date_time(monkeypatch):
    captured = {}

    async def fake_fetch_activity_weather(
        latitude, longitude, activity_date, activity_datetime
    ):
        captured["latitude"] = latitude
        captured["longitude"] = longitude
        captured["activity_date"] = activity_date
        captured["activity_datetime"] = activity_datetime
        return {"weather_temperature_c": 12.3, "weather_condition": "clear"}

    monkeypatch.setattr(
        weather_service,
        "fetch_activity_weather",
        fake_fetch_activity_weather,
    )

    result = await weather_service.enrich_activity_weather(
        {
            "startDateLocal": "2026-05-05T23:30:00",
            "elapsedTime": 7200,
            "startLatlng": [48.0, 11.0],
        },
        streams={
            "time": {"data": [0, 3600, 7200]},
            "latlng": {"data": [[48.0, 11.0], [48.1, 11.1], [48.2, 11.2]]},
        },
    )

    assert captured == {
        "latitude": 48.1,
        "longitude": 11.1,
        "activity_date": "2026-05-06",
        "activity_datetime": "2026-05-06T00:30:00",
    }
    assert result["start_lat"] == 48.1
    assert result["start_lng"] == 11.1
    assert result["weather_temperature_c"] == 12.3


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code,expected",
    [
        (None, None),
        (0, "clear"),
        (1, "partly_cloudy"),
        (3, "cloudy"),
        (45, "fog"),
        (53, "drizzle"),
        (63, "rain"),
        (75, "snow"),
        (95, "thunderstorm"),
        (123, "unknown"),
    ],
)
def test_condition_from_code(code, expected):
    assert weather_service.condition_from_code(code) == expected


@pytest.mark.parametrize(
    "temp,condition,expected",
    [
        (None, None, None),
        (-2, None, "freezing"),
        (5, None, "cold"),
        (36, None, "very_hot"),
        (30, None, "hot"),
        (20, "rain", "rain"),
        (20, "snow", "snow"),
        (20, "clear", None),
    ],
)
def test_weather_load_flag(temp, condition, expected):
    assert weather_service.weather_load_flag(temp, condition) == expected


def test_coordinates_from_activity_variants():
    assert weather_service.coordinates_from_activity({"start_latlng": [52.5, 13.4]}) == (52.5, 13.4)
    assert weather_service.coordinates_from_activity({"startLatlng": [1.0, 2.0]}) == (1.0, 2.0)
    # missing / invalid / null-island all return (None, None)
    assert weather_service.coordinates_from_activity({}) == (None, None)
    assert weather_service.coordinates_from_activity({"start_latlng": [0.0, 0.0]}) == (None, None)
    assert weather_service.coordinates_from_activity({"start_latlng": ["a", "b"]}) == (None, None)
    assert weather_service.coordinates_from_activity({"start_latlng": [1.0]}) == (None, None)


def test_coordinates_from_streams_invalid_inputs():
    assert weather_service.coordinates_from_streams(None) == (None, None)
    assert weather_service.coordinates_from_streams({"latlng": "nope"}) == (None, None)
    assert weather_service.coordinates_from_streams({"latlng": {"data": "x"}}) == (None, None)
    assert weather_service.coordinates_from_streams({"latlng": {"data": [[0, 0]]}}) == (None, None)


def test_coordinates_from_streams_falls_back_to_middle_without_time():
    lat, lng = weather_service.coordinates_from_streams(
        {"latlng": {"data": [[10.0, 20.0], [11.0, 21.0], [12.0, 22.0]]}}
    )
    assert (lat, lng) == (11.0, 21.0)


def test_activity_hour():
    assert weather_service._activity_hour(None) == 12
    assert weather_service._activity_hour("2026-05-05T07:30:00") == 7
    assert weather_service._activity_hour("not-a-date") == 12


def test_nearest_hour_index():
    times = ["2026-05-05T06:00", "2026-05-05T07:00", "2026-05-05T08:00"]
    assert weather_service._nearest_hour_index(times, "2026-05-05", 7) == 1  # exact
    assert weather_service._nearest_hour_index(times, "2026-05-05", 9) == 2  # nearest
    assert weather_service._nearest_hour_index([], "2026-05-05", 7) is None
    assert weather_service._nearest_hour_index(times, "2026-05-06", 7) is None  # other day


def test_pick_hourly_value():
    assert weather_service._pick_hourly_value([1, 2, 3], 1) == 2
    assert weather_service._pick_hourly_value([1, 2, 3], None) is None
    assert weather_service._pick_hourly_value([1, 2, 3], 9) is None
    assert weather_service._pick_hourly_value(None, 0) is None


def test_activity_midpoint_datetime_uses_duration_when_no_stream():
    result = weather_service._activity_midpoint_datetime(
        {"start_date_local": "2026-05-05T10:00:00", "elapsed_time": 3600}, None
    )
    assert result == "2026-05-05T10:30:00"


def test_activity_midpoint_datetime_handles_bad_start():
    assert weather_service._activity_midpoint_datetime({"start_date": "garbage"}, None) == "garbage"
    assert weather_service._activity_midpoint_datetime({}, None) is None


# ---------------------------------------------------------------------------
# httpx-backed functions
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, payload, is_success=True):
        self._payload = payload
        self.is_success = is_success

    def json(self):
        return self._payload


def _patch_httpx(monkeypatch, resp, calls=None):
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
            return resp

    monkeypatch.setattr(weather_service.httpx, "AsyncClient", _FakeClient)


@pytest.mark.asyncio
async def test_fetch_activity_weather_returns_none_for_missing_inputs():
    assert await weather_service.fetch_activity_weather(None, 1.0, "2026-05-05") is None
    assert await weather_service.fetch_activity_weather(1.0, 1.0, "") is None
    assert await weather_service.fetch_activity_weather(1.0, 1.0, "bad-date") is None


@pytest.mark.asyncio
async def test_fetch_activity_weather_recent_uses_forecast(monkeypatch):
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).date().isoformat()
    calls = []
    payload = {
        "hourly": {
            "time": [f"{today}T11:00", f"{today}T12:00"],
            "temperature_2m": [14.2, 15.6],
            "apparent_temperature": [13.0, 14.0],
            "weather_code": [61, 3],
            "wind_speed_10m": [12.0, 10.0],
            "precipitation": [0.4, 0.0],
        }
    }
    _patch_httpx(monkeypatch, _FakeResp(payload), calls)

    result = await weather_service.fetch_activity_weather(52.5, 13.4, today, f"{today}T12:00:00")

    assert calls[0][0] == weather_service.FORECAST_URL
    assert result["weather_temperature_c"] == 15.6
    assert result["weather_condition"] == "cloudy"
    assert result["weather_code"] == 3
    assert result["weather_source"] == "open_meteo"


@pytest.mark.asyncio
async def test_fetch_activity_weather_old_date_uses_archive(monkeypatch):
    calls = []
    payload = {
        "hourly": {
            "time": ["2000-01-01T12:00"],
            "temperature_2m": [5.0],
            "apparent_temperature": [3.0],
            "weather_code": [0],
            "wind_speed_10m": [4.0],
            "precipitation": [0.0],
        }
    }
    _patch_httpx(monkeypatch, _FakeResp(payload), calls)

    result = await weather_service.fetch_activity_weather(52.5, 13.4, "2000-01-01", "2000-01-01T12:00:00")

    assert calls[0][0] == weather_service.ARCHIVE_URL
    assert result["weather_condition"] == "clear"


@pytest.mark.asyncio
async def test_fetch_activity_weather_handles_error_response(monkeypatch):
    _patch_httpx(monkeypatch, _FakeResp({}, is_success=False))
    today = "2000-02-02"
    assert await weather_service.fetch_activity_weather(1.0, 1.0, today) is None


@pytest.mark.asyncio
async def test_fetch_activity_weather_handles_no_hourly(monkeypatch):
    _patch_httpx(monkeypatch, _FakeResp({"hourly": None}))
    assert await weather_service.fetch_activity_weather(1.0, 1.0, "2000-02-02") is None


@pytest.mark.asyncio
async def test_fetch_activity_weather_returns_none_when_no_data_at_index(monkeypatch):
    payload = {"hourly": {"time": ["2000-02-02T12:00"], "temperature_2m": [None], "weather_code": [None]}}
    _patch_httpx(monkeypatch, _FakeResp(payload))
    assert await weather_service.fetch_activity_weather(1.0, 1.0, "2000-02-02", "2000-02-02T12:00:00") is None


@pytest.mark.asyncio
async def test_training_weather_context_empty_without_location(monkeypatch):
    async def fake_loc(db, user_id):
        return None

    monkeypatch.setattr(weather_service.crud, "get_latest_ride_metric_with_location", fake_loc)
    assert await weather_service.training_weather_context_for_user(None, "u1") == ""


@pytest.mark.asyncio
async def test_training_weather_context_renders_forecast(monkeypatch):
    class _Loc:
        start_lat = 52.5
        start_lng = 13.4

    async def fake_loc(db, user_id):
        return _Loc()

    monkeypatch.setattr(weather_service.crud, "get_latest_ride_metric_with_location", fake_loc)
    payload = {
        "daily": {
            "time": ["2026-05-05", "2026-05-06"],
            "temperature_2m_max": [36.0, 2.0],
            "temperature_2m_min": [22.0, -3.0],
            "weather_code": [0, 75],
            "precipitation_sum": [0.0, 5.0],
            "wind_speed_10m_max": [10.0, 40.0],
        }
    }
    _patch_httpx(monkeypatch, _FakeResp(payload))

    result = await weather_service.training_weather_context_for_user(None, "u1", days=2)

    assert "Upcoming weather" in result
    assert "training flag:very_hot" in result
    assert "snow" in result
    assert "wind 40kph" in result


@pytest.mark.asyncio
async def test_training_weather_context_handles_error(monkeypatch):
    class _Loc:
        start_lat = 1.0
        start_lng = 1.0

    monkeypatch.setattr(
        weather_service.crud, "get_latest_ride_metric_with_location",
        lambda db, user_id: _async_return(_Loc()),
    )
    _patch_httpx(monkeypatch, _FakeResp({}, is_success=False))
    assert await weather_service.training_weather_context_for_user(None, "u1") == ""


async def _async_return(value):
    return value


@pytest.mark.asyncio
async def test_backfill_missing_ride_weather_applies_weather(monkeypatch):
    import models

    row = models.RideMetric(
        strava_activity_id=123,
        start_lat=52.5,
        start_lng=13.4,
        activity_start_datetime="2026-05-05T10:00:00",
        duration_seconds=3600,
        activity_date="2026-05-05",
    )

    async def fake_rows(db, user_id, limit=90):
        return [row]

    async def fake_weather(lat, lng, activity_date, activity_datetime=None):
        return {"weather_temperature_c": 18.0, "weather_source": "open_meteo"}

    monkeypatch.setattr(weather_service.crud, "get_ride_metrics_missing_weather", fake_rows)
    monkeypatch.setattr(weather_service, "fetch_activity_weather", fake_weather)

    class _DB:
        async def flush(self):
            self.flushed = True

    db = _DB()
    updated = await weather_service.backfill_missing_ride_weather(db, "u1", access_token=None)

    assert updated == 1
    assert row.weather_temperature_c == 18.0


@pytest.mark.asyncio
async def test_backfill_recovers_coords_from_stream(monkeypatch):
    import models

    row = models.RideMetric(
        strava_activity_id=99,
        start_lat=None,
        start_lng=None,
        activity_start_datetime="2026-05-05T10:00:00",
        duration_seconds=1800,
        activity_date="2026-05-05",
    )

    async def fake_rows(db, user_id, limit=90):
        return [row]

    async def fake_streams(token, activity_id):
        return {"latlng": {"data": [[40.0, 10.0], [41.0, 11.0], [42.0, 12.0]]}}

    async def fake_weather(lat, lng, activity_date, activity_datetime=None):
        return {"weather_temperature_c": 9.0} if lat is not None else None

    monkeypatch.setattr(weather_service.crud, "get_ride_metrics_missing_weather", fake_rows)
    monkeypatch.setattr(weather_service, "fetch_activity_streams", fake_streams)
    monkeypatch.setattr(weather_service, "fetch_activity_weather", fake_weather)

    class _DB:
        async def flush(self):
            pass

    updated = await weather_service.backfill_missing_ride_weather(_DB(), "u1", access_token="tok")
    assert updated == 1
    assert row.start_lat == 41.0
