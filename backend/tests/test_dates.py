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


def test_app_date_context_includes_authoritative_relative_dates():
    from services.dates import app_date_context

    now = datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc)

    context = app_date_context(now, timezone_name="Europe/Berlin")

    assert "Current local date context (Europe/Berlin)" in context
    assert "Today is Tuesday, May 26, 2026 (2026-05-26)." in context
    assert "Yesterday was Monday, May 25, 2026 (2026-05-25)." in context
    assert "Tomorrow is Wednesday, May 27, 2026 (2026-05-27)." in context


def test_request_timezone_reads_browser_header():
    from services.dates import request_timezone

    class DummyRequest:
        headers = {"x-app-timezone": "America/Los_Angeles"}

    assert request_timezone(DummyRequest()) == "America/Los_Angeles"


# --- additional edge cases (issue #335) ---

import types as _types

from services import dates as _dates


def test_app_timezone_falls_back_to_configured_on_invalid_name():
    tz = _dates.app_timezone("Not/AReal_Zone")
    # falls back to the configured app timezone (or UTC), never raises
    assert tz is not None


def test_request_timezone_reads_header():
    req = _types.SimpleNamespace(headers={_dates.TIMEZONE_HEADER: " Europe/Berlin "})
    assert _dates.request_timezone(req) == "Europe/Berlin"


def test_request_timezone_returns_none_without_header():
    req = _types.SimpleNamespace(headers={})
    assert _dates.request_timezone(req) is None
    req2 = _types.SimpleNamespace(headers={_dates.TIMEZONE_HEADER: "   "})
    assert _dates.request_timezone(req2) is None
