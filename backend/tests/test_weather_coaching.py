"""Weather as a coaching input: prompts, narration, and the chat capture (#495)."""

import pytest

import crud
import services.weather_service as weather_service
from services.home_location import TrainingLocation
from services.prompts import (
    TRAINING_PLAN_PRINCIPLES,
    ask_trainer_plan_updates_rule,
    ask_trainer_system,
    plan_change_summary_user,
)

_WEATHER_SECTION = (
    "Upcoming weather near Freiburg:\n"
    "- 2026-08-01 | clear | 24-39C | training flag:very_hot\n"
    "\n"
    "What is known about this athlete's own weather tolerances: heat-tolerant "
    "(confidence 0.65, 9 observations)"
)


def _prompt(weather_context_section: str = "") -> str:
    return ask_trainer_system(
        profile={"currentFTP": 280},
        today="2026-08-01",
        last_7_days=[],
        next_n_days=[
            {
                "date": "2026-08-01",
                "workoutType": "intervals",
                "title": "VO2max 5x4",
                "durationMinutes": 75,
            }
        ],
        assessment_section="",
        memory_section="",
        workout_section="",
        plan_updates_rule=ask_trainer_plan_updates_rule(None),
        weather_context_section=weather_context_section,
    )


def test_coach_prompt_carries_the_forecast_and_the_learned_tolerances():
    """The coach chat is where "should I ride tomorrow?" is asked, so both the
    forecast and what is known about this athlete must reach it."""
    prompt = _prompt(_WEATHER_SECTION)

    assert "Upcoming weather near Freiburg" in prompt
    assert "training flag:very_hot" in prompt
    assert "heat-tolerant" in prompt


def test_coach_prompt_ties_weather_moves_to_the_learned_tolerance():
    prompt = _prompt(_WEATHER_SECTION)

    assert "Weather-aware scheduling rules" in prompt
    assert "learned tolerances" in prompt
    assert "demonstrably handle well" in prompt
    # The athlete must be able to trace a weather-driven change back to a reason.
    assert "say so" in prompt


def test_coach_prompt_omits_weather_rules_without_a_forecast():
    """No forecast, no weather instructions — the prompt must not invent context."""
    prompt = _prompt("")

    assert "Weather-aware scheduling rules" not in prompt
    assert "Upcoming weather" not in prompt


def test_plan_principles_condition_weather_on_learned_tolerances():
    assert "learned tolerances" in TRAINING_PLAN_PRINCIPLES


def test_plan_change_narration_can_explain_a_weather_driven_move():
    """Without the forecast, a session moved off a 39 C day reads as churn (#439)."""
    prompt = plan_change_summary_user(
        [
            {
                "date": "2026-08-01",
                "old_day": {"workoutType": "intervals", "title": "VO2max 5x4"},
                "new_day": {"workoutType": "endurance", "title": "Easy spin"},
            }
        ],
        {"currentFTP": 280},
        run_context="an automatic plan adjustment",
        weather_context_section=_WEATHER_SECTION,
    )

    assert "training flag:very_hot" in prompt
    assert "name the condition" in prompt
    assert "never invent a weather reason" in prompt


def test_plan_change_narration_omits_the_weather_rule_without_a_forecast():
    prompt = plan_change_summary_user(
        [{"date": "2026-08-01", "old_day": {}, "new_day": {}}],
        {},
        run_context="an automatic plan adjustment",
    )

    assert "weather" not in prompt.lower()


# ---------------------------------------------------------------------------
# Chat capture through the real endpoint
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_forecast_cache():
    weather_service.clear_forecast_cache()
    yield
    weather_service.clear_forecast_cache()


@pytest.mark.asyncio
async def test_stated_location_is_persisted_and_confirmed_in_the_reply(
    client, auth_headers, mock_ai_service, monkeypatch
):
    """The router moves the location before the model replies, so the confirmation
    is deterministic rather than dependent on the model's prose (#437 pattern)."""
    from services import home_location

    async def fake_geocode(name):
        assert name == "Freiburg"
        return 47.99, 7.85, "Freiburg"

    monkeypatch.setattr(home_location, "geocode_place", fake_geocode)

    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "I mostly train near Freiburg now, what's next?"},
    )

    assert response.status_code == 200
    assert "usual training location to Freiburg" in response.json()["response"]

    stored = await client.get("/api/v1/users/me/home-location", headers=auth_headers)
    assert stored.json()["location"]["source"] == "user_set"


@pytest.mark.asyncio
async def test_stated_weather_preference_becomes_a_belief(
    client, auth_headers, mock_ai_service
):
    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "I actually love the rain, keep the outdoor sessions"},
    )
    assert response.status_code == 200

    hypotheses = await client.get(
        "/api/v1/users/me/athlete-hypotheses", headers=auth_headers
    )
    assert hypotheses.status_code == 200
    rain = [
        h
        for h in hypotheses.json()["hypotheses"]
        if h["category"] == "weather_preference"
    ]
    assert len(rain) == 1
    assert "rain" in rain[0]["statement"].lower()


@pytest.mark.asyncio
async def test_ordinary_question_leaves_location_and_beliefs_untouched(
    client, auth_headers, mock_ai_service
):
    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "How did my ride yesterday look?"},
    )
    assert response.status_code == 200
    assert "usual training location" not in response.json()["response"]

    stored = await client.get("/api/v1/users/me/home-location", headers=auth_headers)
    assert stored.json()["location"] is None


@pytest.mark.asyncio
async def test_forecast_reaches_the_coach_prompt(
    client, auth_headers, mock_ai_service, monkeypatch
):
    """End-to-end: a stored location plus a forecast must land in the system prompt
    the coach is actually asked with."""
    await client.put(
        "/api/v1/users/me/home-location",
        headers=auth_headers,
        json={"latitude": 47.99, "longitude": 7.85, "label": "Freiburg"},
    )

    async def fake_forecast(latitude, longitude, days=14, *, now=None):
        return [
            {
                "date": "2026-08-01",
                "condition": "clear",
                "weather_code": 0,
                "temperature_max_c": 39.0,
                "temperature_min_c": 24.0,
                "precipitation_mm": 0.0,
                "wind_speed_kph": 8.0,
                "load_flag": "very_hot",
            }
        ]

    monkeypatch.setattr(weather_service, "fetch_daily_forecast", fake_forecast)

    captured: dict = {}
    original = mock_ai_service["ask_trainer"]

    async def spy(*args, **kwargs):
        captured.update(kwargs)
        return await original(*args, **kwargs)

    monkeypatch.setattr("services.ai_service.ask_trainer", spy)

    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "Is tomorrow's session still sensible?"},
    )

    assert response.status_code == 200
    assert "very_hot" in captured["weather_context_section"]
    assert "Freiburg" in captured["weather_context_section"]
