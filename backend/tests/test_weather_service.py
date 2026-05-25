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
