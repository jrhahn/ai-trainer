from datetime import datetime, timezone


def test_app_today_uses_configured_timezone(monkeypatch):
    from config import settings
    from services.dates import app_today

    monkeypatch.setattr(settings, "app_timezone", "Europe/Berlin")

    now = datetime(2026, 5, 17, 22, 30, tzinfo=timezone.utc)

    assert app_today(now).isoformat() == "2026-05-18"


def test_app_today_falls_back_to_utc_for_invalid_timezone(monkeypatch):
    from config import settings
    from services.dates import app_today

    monkeypatch.setattr(settings, "app_timezone", "No/Such_Zone")

    now = datetime(2026, 5, 17, 22, 30, tzinfo=timezone.utc)

    assert app_today(now).isoformat() == "2026-05-17"


def test_app_today_prefers_explicit_timezone(monkeypatch):
    from config import settings
    from services.dates import app_today

    monkeypatch.setattr(settings, "app_timezone", "UTC")

    now = datetime(2026, 5, 17, 22, 30, tzinfo=timezone.utc)

    assert app_today(now, timezone_name="Europe/Berlin").isoformat() == "2026-05-18"


def test_request_timezone_reads_browser_header():
    from services.dates import request_timezone

    class DummyRequest:
        headers = {"x-app-timezone": "America/Los_Angeles"}

    assert request_timezone(DummyRequest()) == "America/Los_Angeles"
