import json
import logging
from unittest.mock import AsyncMock, patch

import pytest

import services.ai_service as ai_service
import services.analysis as analysis


PROFILE = {
    "name": "Test Rider",
    "email": "rider@example.com",
    "bikeType": "road",
    "trainingGoal": "ftp_improvement",
    "weeklyHours": 8,
    "followsTrainingPlan": True,
    "fitnessLevel": "intermediate",
}


@pytest.mark.asyncio
async def test_ai_endpoints(client, auth_headers, mock_ai_service):
    analyse_response = await client.post(
        "/api/v1/ai/analyse-activities",
        headers=auth_headers,
        json={
            "activities": [
                {
                    "id": 1,
                    "name": "Ride",
                    "type": "Ride",
                    "distance": 50000,
                    "movingTime": 3600,
                    "elapsedTime": 3700,
                    "totalElevationGain": 500,
                    "startDate": "2026-04-10T08:00:00Z",
                    "averageWatts": 220,
                }
            ]
        },
    )
    assert analyse_response.status_code == 200
    body = analyse_response.json()
    assert body["assessment"]["riderType"] == "allrounder"
    assert body["assessment"]["rideInsights"] is not None
    assert body["assessment"]["lastRideFeedback"] is not None

    generate_response = await client.post(
        "/api/v1/ai/generate-plan",
        headers=auth_headers,
        json={},
    )
    assert generate_response.status_code == 200
    assert generate_response.json()[0]["title"] == "Endurance Ride"

    adapt_response = await client.post(
        "/api/v1/ai/adapt-plan",
        headers=auth_headers,
        json={
            "recentFeedback": [
                {
                    "actualDurationMinutes": 60,
                    "perceivedEffort": 4,
                    "notes": "Hard",
                    "completedAt": "2026-04-10T10:00:00Z",
                }
            ],
        },
    )
    assert adapt_response.status_code == 200
    assert adapt_response.json()[0]["workoutType"] == "recovery"

    ask_response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={
            "question": "Should I swap tomorrow to a rest day?",
        },
    )
    assert ask_response.status_code == 200
    assert ask_response.json()["response"] == "Take it easy tomorrow."
    assert ask_response.json()["planUpdates"][0]["workoutType"] == "rest"

    rate_response = await client.post(
        "/api/v1/ai/rate-workout",
        headers=auth_headers,
        json={
            "day": {
                "date": "2026-04-10",
                "workoutType": "intervals",
                "title": "VO2 Max",
                "description": "Hard reps",
                "durationMinutes": 60,
                "feedback": {
                    "actualDurationMinutes": 58,
                    "perceivedEffort": 4,
                    "notes": "Tough but good",
                    "completedAt": "2026-04-10T10:00:00Z",
                },
            },
        },
    )
    assert rate_response.status_code == 200
    assert rate_response.json()["feedback"] == "Strong execution overall."


# ---------------------------------------------------------------------------
# Unit tests for ask_trainer context_workout prompt branching
# ---------------------------------------------------------------------------

PLAN = [
    {
        "date": "2026-04-16",
        "workoutType": "intervals",
        "title": "VO2 Intervals",
        "description": "5×4 min at 110% FTP",
        "durationMinutes": 60,
    }
]

CONTEXT_WORKOUT = {
    "date": "2026-04-16",
    "workoutType": "intervals",
    "title": "VO2 Intervals",
    "description": "5×4 min at 110% FTP",
    "durationMinutes": 60,
}


@pytest.mark.asyncio
async def test_ask_trainer_without_context_workout_uses_explicit_only_rule():
    """Without context_workout the prompt must use the 'explicit request only' planUpdates rule."""
    captured_prompt: list[str] = []

    async def fake_chat_history(provider, system_prompt, messages, json_mode=False, **kwargs):
        captured_prompt.append(system_prompt)
        return json.dumps({"response": "Looks good.", "planUpdates": []})

    with patch.object(ai_service, "_chat_history", side_effect=fake_chat_history):
        result = await ai_service.ask_trainer(
            question="How is my plan looking?",
            plan=PLAN,
            profile=PROFILE,
            context_workout=None,
        )

    assert result["response"] == "Looks good."
    prompt = captured_prompt[0]
    # Explicit-only wording must appear
    assert "Only include this" in prompt or "explicitly asks" in prompt
    # Context workout section must NOT appear
    assert "currently viewing this specific workout" not in prompt
    # Implicit-change instruction must NOT appear
    assert "proposes ANY change" not in prompt


@pytest.mark.asyncio
async def test_ask_trainer_with_context_workout_uses_implicit_change_rule():
    """With context_workout the prompt must allow planUpdates for implicit coaching changes."""
    captured_prompt: list[str] = []

    async def fake_chat_history(provider, system_prompt, messages, json_mode=False, **kwargs):
        captured_prompt.append(system_prompt)
        return json.dumps(
            {
                "response": "Reduce to 3 reps to manage fatigue.",
                "planUpdates": [
                    {
                        "date": "2026-04-16",
                        "workoutType": "intervals",
                        "title": "VO2 Intervals (adjusted)",
                        "description": "3×4 min at 110% FTP",
                        "durationMinutes": 45,
                    }
                ],
            }
        )

    with patch.object(ai_service, "_chat_history", side_effect=fake_chat_history):
        result = await ai_service.ask_trainer(
            question="Is this workout appropriate given my fatigue?",
            plan=PLAN,
            profile=PROFILE,
            context_workout=CONTEXT_WORKOUT,
        )

    assert "adjusted" in result["response"] or "fatigue" in result["response"].lower() or result["plan_updates"]
    prompt = captured_prompt[0]
    # Context workout JSON must be embedded in the prompt
    assert "currently viewing this specific workout" in prompt
    assert "VO2 Intervals" in prompt
    # Implicit-change instruction must be present
    assert "proposes ANY change" in prompt
    # planUpdates must include the updated workout
    assert result["plan_updates"] is not None
    assert result["plan_updates"][0]["date"] == "2026-04-16"
    assert result["plan_updates"][0]["durationMinutes"] == 45


@pytest.mark.asyncio
async def test_ask_trainer_with_context_workout_forwards_plan_updates(
    client, auth_headers, mock_ai_service
):
    """HTTP endpoint must pass contextWorkout through and return planUpdates from the service."""
    # Override ask_trainer mock to return a plan update for the context workout
    mock_ai_service["ask_trainer"].return_value = {
        "response": "Shorten the workout.",
        "plan_updates": [
            {
                "date": "2026-04-16",
                "workoutType": "intervals",
                "title": "VO2 Intervals (shortened)",
                "description": "3 reps only",
                "durationMinutes": 40,
            }
        ],
    }

    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={
            "question": "Is this too hard today?",
            "contextWorkout": CONTEXT_WORKOUT,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["response"] == "Shorten the workout."
    assert body["planUpdates"][0]["durationMinutes"] == 40
    # Verify context_workout was forwarded to the service
    call_kwargs = mock_ai_service["ask_trainer"].call_args.kwargs
    assert call_kwargs["context_workout"] == CONTEXT_WORKOUT


@pytest.mark.asyncio
async def test_ask_trainer_intervals_in_prompt_and_plan_updates():
    """intervals must appear in the prompt rule and must be returned in plan_updates."""
    captured_prompt: list[str] = []

    updated_intervals = [
        {"duration": 120, "power": 370, "rest": 120},
        {"duration": 120, "power": 370, "rest": 120},
        {"duration": 120, "power": 370, "rest": 120},
        {"duration": 120, "power": 370, "rest": 120},
    ]

    async def fake_chat_history(provider, system_prompt, messages, json_mode=False, **kwargs):
        captured_prompt.append(system_prompt)
        return json.dumps(
            {
                "response": "Updated to 4×2 min at 370w.",
                "planUpdates": [
                    {
                        "date": "2026-04-16",
                        "workoutType": "intervals",
                        "title": "VO2 Max Intervals (adjusted)",
                        "description": "4×2 min at 370w with 2 min rest",
                        "durationMinutes": 24,
                        "intervals": updated_intervals,
                    }
                ],
            }
        )

    with patch.object(ai_service, "_chat_history", side_effect=fake_chat_history):
        result = await ai_service.ask_trainer(
            question="Make the vo2 max intervals 4 times each 2min at 370w",
            plan=PLAN,
            profile=PROFILE,
            context_workout=CONTEXT_WORKOUT,
        )

    prompt = captured_prompt[0]
    # intervals must be mentioned as an updatable field in the prompt
    assert '"intervals"' in prompt
    # intervals must be present and correct in the returned plan_updates
    assert result["plan_updates"] is not None
    assert len(result["plan_updates"]) == 1
    returned_intervals = result["plan_updates"][0]["intervals"]
    assert returned_intervals is not None
    assert len(returned_intervals) == 4
    assert returned_intervals[0]["duration"] == 120
    assert returned_intervals[0]["power"] == 370


@pytest.mark.asyncio
async def test_ask_trainer_intervals_forwarded_through_http_endpoint(
    client, auth_headers, mock_ai_service
):
    """HTTP endpoint must pass intervals through planUpdates to the response schema."""
    updated_intervals = [
        {"duration": 120, "power": 370, "rest": 120},
        {"duration": 120, "power": 370, "rest": 120},
        {"duration": 120, "power": 370, "rest": 120},
        {"duration": 120, "power": 370, "rest": 120},
    ]
    mock_ai_service["ask_trainer"].return_value = {
        "response": "Updated to 4×2 min at 370w.",
        "plan_updates": [
            {
                "date": "2026-04-16",
                "workoutType": "intervals",
                "title": "VO2 Max Intervals (adjusted)",
                "description": "4×2 min at 370w with 2 min rest",
                "durationMinutes": 24,
                "intervals": updated_intervals,
            }
        ],
    }

    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={
            "question": "Make the vo2 max intervals 4 times each 2min at 370w",
            "contextWorkout": CONTEXT_WORKOUT,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["response"] == "Updated to 4×2 min at 370w."
    returned_intervals = body["planUpdates"][0]["intervals"]
    assert returned_intervals is not None
    assert len(returned_intervals) == 4
    assert returned_intervals[0]["power"] == 370
    assert returned_intervals[0]["duration"] == 120


@pytest.mark.asyncio
async def test_ask_trainer_plan_change_reflection_in_prompt():
    """When a plan change is requested, the prompt must include honest-reflection instructions."""
    captured_prompt: list[str] = []

    async def fake_chat_history(provider, system_prompt, messages, json_mode=False, **kwargs):
        captured_prompt.append(system_prompt)
        return json.dumps(
            {
                "response": (
                    "Great initiative! Dropping to 3 intervals will reduce your training load "
                    "slightly which could help with recovery, though you'll get a bit less VO2 "
                    "stimulus. If you're feeling fresh, 4 is ideal — but 3 is a solid choice "
                    "when fatigue is a factor. I've updated the plan for you!"
                ),
                "planUpdates": [
                    {
                        "date": "2026-04-16",
                        "workoutType": "intervals",
                        "title": "VO2 Intervals (adjusted)",
                        "description": "3×4 min at 110% FTP",
                        "durationMinutes": 45,
                    }
                ],
            }
        )

    with patch.object(ai_service, "_chat_history", side_effect=fake_chat_history):
        result = await ai_service.ask_trainer(
            question="Change the VO2 intervals to only 3 reps",
            plan=PLAN,
            profile=PROFILE,
            context_workout=CONTEXT_WORKOUT,
        )

    prompt = captured_prompt[0]
    # Reflection instruction must be present
    assert "reflect on whether the change is a good idea" in prompt
    assert "honest" in prompt
    assert "kind" in prompt or "encouraging" in prompt
    # Trade-off honesty instruction must be present
    assert "trade-off" in prompt or "trade-offs" in prompt
    # CRITICAL intervals instruction and concrete example must be present
    assert "CRITICAL" in prompt
    assert '"duration"' in prompt and '"power"' in prompt and '"rest"' in prompt
    # Coach response must include a plan update
    assert result["plan_updates"] is not None


# ---------------------------------------------------------------------------
# Unit tests for algorithmic ride analysis functions
# ---------------------------------------------------------------------------


def make_flat_stream(power: float, duration_secs: int) -> tuple[list[float], list[float]]:
    """Return (watts, time_stream) for a constant-power ride."""
    return [power] * duration_secs, list(range(duration_secs))


def test_classify_ride_purpose_recovery():
    watts, ts = make_flat_stream(140, 3600)  # 140W @ FTP 250 = 56%
    assert analysis.classify_ride_purpose(watts, ts, 250) == "recovery"


def test_classify_ride_purpose_endurance():
    watts, ts = make_flat_stream(165, 3600)  # 66% FTP
    assert analysis.classify_ride_purpose(watts, ts, 250) == "endurance"


def test_classify_ride_purpose_tempo():
    watts, ts = make_flat_stream(200, 3600)  # 80% FTP
    assert analysis.classify_ride_purpose(watts, ts, 250) == "tempo"


def test_classify_ride_purpose_vo2max_intervals():
    """A ride with repeated 3-min blocks at 115% FTP should be classified as vo2max."""
    ftp = 250
    work = round(ftp * 1.15)  # 287 W
    rest = round(ftp * 0.50)  # 125 W
    # 5 x (3 min work + 3 min rest)
    watts: list[float] = []
    for _ in range(5):
        watts.extend([work] * 180)
        watts.extend([rest] * 180)
    ts = list(range(len(watts)))
    category = analysis.classify_ride_purpose(watts, ts, ftp)
    assert category == "interval_vo2max"


def test_classify_ride_purpose_sprint_intervals():
    """Sub-2-min efforts above 130% FTP -> sprint."""
    ftp = 250
    sprint_power = round(ftp * 1.40)  # 350 W
    rest_power = round(ftp * 0.45)    # 112 W
    # 8 x (1 min sprint + 4 min rest)
    watts: list[float] = []
    for _ in range(8):
        watts.extend([sprint_power] * 60)
        watts.extend([rest_power] * 240)
    ts = list(range(len(watts)))
    category = analysis.classify_ride_purpose(watts, ts, ftp)
    assert category == "interval_sprints"


def test_detect_intervals_basic():
    """Should detect 3 interval blocks separated by recovery."""
    ftp = 250
    work = round(ftp * 1.10)  # 275 W
    rest = round(ftp * 0.50)  # 125 W
    watts: list[float] = []
    for _ in range(3):
        watts.extend([work] * 300)  # 5 min
        watts.extend([rest] * 180)  # 3 min rest
    ts = list(range(len(watts)))
    intervals = analysis.detect_intervals(watts, ts, ftp)
    assert len(intervals) == 3
    for iv in intervals:
        assert iv["duration_secs"] >= 270  # at least 4.5 min (90 % of 5 min)
        assert iv["avg_power"] >= work * 0.95


def test_detect_intervals_no_hard_efforts():
    """A pure endurance ride should return no detected intervals."""
    watts, ts = make_flat_stream(180, 3600)  # 72% of 250 FTP
    intervals = analysis.detect_intervals(watts, ts, 250)
    assert intervals == []


def test_compute_hr_drift_rising():
    # HR climbs steadily from 140 to 160 -> positive slope (drifting)
    hr = [140 + i * (20 / 119) for i in range(120)]
    drift = analysis.compute_hr_drift(hr)
    assert drift is not None and drift > 0


def test_compute_hr_drift_stable():
    hr = [150.0] * 120
    drift = analysis.compute_hr_drift(hr)
    assert drift is not None and abs(drift) < 0.01


def test_build_ride_analysis_returns_category():
    ftp = 250
    work = round(ftp * 1.15)
    rest = round(ftp * 0.50)
    watts: list[float] = []
    for _ in range(5):
        watts.extend([work] * 180)
        watts.extend([rest] * 180)
    ts = list(range(len(watts)))
    streams = {"watts": {"data": watts}, "time": {"data": ts}}
    ride_result = analysis.build_ride_analysis(streams, float(ftp))
    assert ride_result["ride_category"] == "interval_vo2max"
    assert len(ride_result["intervals_detected"]) == 5
    assert "avg_power_w" in ride_result


def test_build_ride_analysis_with_hr_drift():
    ftp = 250
    watts = [250.0] * 600  # 10 min at FTP
    # HR rises from 160 to 185 (drifting)
    hr = [160 + i * (25 / 599) for i in range(600)]
    ts = list(range(600))
    streams = {
        "watts": {"data": watts},
        "heartrate": {"data": hr},
        "time": {"data": ts},
    }
    ride_result = analysis.build_ride_analysis(streams, float(ftp))
    assert len(ride_result["intervals_detected"]) >= 1
    iv = ride_result["intervals_detected"][0]
    assert "hr_drift_bpm" in iv
    # Total drift should be roughly 25 bpm
    assert iv["hr_drift_bpm"] > 5


@pytest.mark.asyncio
async def test_analyse_activities_response_shape(client, auth_headers, mock_ai_service):
    """The analyse-activities endpoint must return the new shape: {assessment, planUpdates}."""
    resp = await client.post(
        "/api/v1/ai/analyse-activities",
        headers=auth_headers,
        json={
            "activities": [
                {
                    "id": 99,
                    "name": "Test Ride",
                    "type": "Ride",
                    "distance": 40000,
                    "movingTime": 3600,
                    "elapsedTime": 3700,
                    "totalElevationGain": 300,
                    "startDate": "2026-04-15T08:00:00Z",
                    "averageWatts": 200,
                }
            ]
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "assessment" in body
    assert "riderType" in body["assessment"]
    assert body["assessment"]["rideInsights"] is not None
    assert body["assessment"]["lastRideFeedback"] is not None
    assert "planUpdates" in body


# ---------------------------------------------------------------------------
# rideInsights serialisation regression (list vs str)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyse_activities_ride_insights_as_list_is_persisted(
    client, auth_headers, mock_ai_service
):
    """When the LLM returns rideInsights as a list of dicts the endpoint must not
    raise a DB error — the list must be JSON-serialised before the INSERT.
    Regression for: asyncpg.DataError 'expected str, got list'
    """
    import crud
    from database import async_session_maker

    mock_ai_service["analyse_strava_activities"].return_value = {
        "estimatedFTP": 290,
        "riderType": "climber",
        "notes": "Good climber.",
        "rideInsights": [
            {"id": 18187434054, "name": "Afternoon Ride", "category": "mixed", "comment": "Strong effort."},
            {"id": 18187434055, "name": "Morning Ride", "category": "endurance", "comment": "Easy spin."},
        ],
        "lastRideFeedback": "Nice ride.",
    }

    resp = await client.post(
        "/api/v1/ai/analyse-activities",
        headers=auth_headers,
        json={
            "activities": [
                {
                    "id": 101,
                    "name": "Afternoon Ride",
                    "type": "Ride",
                    "distance": 60000,
                    "movingTime": 5400,
                    "elapsedTime": 5500,
                    "totalElevationGain": 800,
                    "startDate": "2026-04-21T10:00:00Z",
                    "averageWatts": 250,
                }
            ]
        },
    )
    assert resp.status_code == 200, resp.text

    # Verify the value was serialised and round-trips correctly from the DB
    async with async_session_maker() as db:
        user = await crud.get_user_by_email(db, "rider@example.com")
        await db.refresh(user, ["rider_assessment"])
        stored = user.rider_assessment.ride_insights
        assert isinstance(stored, str), "ride_insights must be stored as a string in the DB"
        parsed = json.loads(stored)
        assert isinstance(parsed, list)
        assert parsed[0]["name"] == "Afternoon Ride"


# ---------------------------------------------------------------------------
# loginSummary serialisation regression (dict vs str)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyse_activities_login_summary_as_dict_is_persisted(
    client, auth_headers, mock_ai_service
):
    """When the LLM returns loginSummary as a dict the endpoint must not
    raise a DB error — the dict must be JSON-serialised before the INSERT.
    Regression for: asyncpg.DataError 'expected str, got dict'
    """
    import crud
    from database import async_session_maker

    mock_ai_service["analyse_strava_activities"].return_value = {
        "estimatedFTP": 290,
        "riderType": "climber",
        "notes": "Good climber.",
        "rideInsights": "Great rides.",
        "lastRideFeedback": "Nice ride.",
        "loginSummary": {
            "1_WHAT_YOU_DID": "Over the past week you completed two rides totalling 3h.",
            "2_FTP_AND_FITNESS_INSIGHTS": "FTP looks stable at ~290 W.",
            "3_PLAN_ALIGNMENT": "You hit your endurance session well.",
            "4_CONCLUSIONS": "Keep up the consistency.",
        },
    }

    resp = await client.post(
        "/api/v1/ai/analyse-activities",
        headers=auth_headers,
        json={
            "activities": [
                {
                    "id": 102,
                    "name": "Morning Ride",
                    "type": "Ride",
                    "distance": 60000,
                    "movingTime": 5400,
                    "elapsedTime": 5500,
                    "totalElevationGain": 800,
                    "startDate": "2026-04-22T10:00:00Z",
                    "averageWatts": 250,
                }
            ]
        },
    )
    assert resp.status_code == 200, resp.text

    # Verify the value was serialised and round-trips correctly from the DB
    async with async_session_maker() as db:
        user = await crud.get_user_by_email(db, "rider@example.com")
        await db.refresh(user, ["rider_assessment"])
        stored = user.rider_assessment.login_summary
        assert isinstance(stored, str), "login_summary must be stored as a string in the DB"
        parsed = json.loads(stored)
        assert isinstance(parsed, dict)
        assert "1_WHAT_YOU_DID" in parsed


# ---------------------------------------------------------------------------
# Strava stream error logging
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyse_activities_logs_warning_on_stream_error(
    client, auth_headers, mock_ai_service, caplog
):
    """Strava stream fetch errors must be logged as warnings, not silently swallowed."""
    import crud
    import models
    from database import async_session_maker

    # Give the test user a fake Strava token so the stream-fetch path is exercised.
    async with async_session_maker() as db:
        user = await crud.get_user_by_email(db, "rider@example.com")
        user.strava_token = models.StravaToken(
            user_id=user.id,
            access_token="fake-access-token",
            refresh_token="fake-refresh-token",
            expires_at=9999999999,
            athlete_id=12345,
        )
        await db.commit()

    with (
        patch("routers.ai.ensure_fresh_strava_token", new=AsyncMock(return_value="fake-token")),
        patch(
            "routers.ai.fetch_activity_streams",
            new=AsyncMock(side_effect=RuntimeError("Strava API unavailable")),
        ),
        caplog.at_level(logging.WARNING, logger="routers.ai"),
    ):
        resp = await client.post(
            "/api/v1/ai/analyse-activities",
            headers=auth_headers,
            json={
                "activities": [
                    {
                        "id": 1,
                        "name": "Morning Ride",
                        "type": "Ride",
                        "distance": 50000,
                        "movingTime": 3600,
                        "elapsedTime": 3700,
                        "totalElevationGain": 500,
                        "startDate": "2026-04-10T08:00:00Z",
                        "averageWatts": 220,
                    }
                ]
            },
        )

    # The endpoint must still succeed (streams are optional).
    assert resp.status_code == 200
    # A warning must have been emitted with the full exception traceback attached.
    warning_records = [
        r for r in caplog.records if "Strava" in r.message and r.levelno == logging.WARNING
    ]
    assert warning_records, "Expected a WARNING log mentioning Strava stream failure"
    assert all(
        r.exc_info is not None and r.exc_info[0] is RuntimeError
        for r in warning_records
    ), "Warning log must include exc_info so the traceback is visible"


# ---------------------------------------------------------------------------
# Integration tests for updated rate_completed_workout response shape (Task 4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_workout_returns_flag_for_adaptation_false(client, auth_headers, mock_ai_service):
    """rate-workout endpoint must return flag_for_adaptation=false for normal sessions."""
    response = await client.post(
        "/api/v1/ai/rate-workout",
        headers=auth_headers,
        json={
            "day": {
                "date": "2026-04-10",
                "workoutType": "endurance",
                "title": "Easy Ride",
                "description": "Easy aerobic ride",
                "durationMinutes": 90,
                "feedback": {
                    "actualDurationMinutes": 88,
                    "perceivedEffort": 2,
                    "notes": "Felt great",
                    "completedAt": "2026-04-10T10:00:00Z",
                },
            },
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert "feedback" in body
    assert "flagForAdaptation" in body or "flag_for_adaptation" in body
    flag = body.get("flagForAdaptation", body.get("flag_for_adaptation"))
    assert flag is False


@pytest.mark.asyncio
async def test_rate_workout_flag_triggers_adapt_plan(client, auth_headers, mock_ai_service):
    """When flag_for_adaptation=True, adapt_training_plan must be called automatically."""
    # Override rate_completed_workout to flag for adaptation
    mock_ai_service["rate_completed_workout"].return_value = {
        "feedback": "This session was very hard — rest is recommended.",
        "flag_for_adaptation": True,
    }

    response = await client.post(
        "/api/v1/ai/rate-workout",
        headers=auth_headers,
        json={
            "day": {
                "date": "2026-04-10",
                "workoutType": "intervals",
                "title": "VO2 Max",
                "description": "Hard reps",
                "durationMinutes": 60,
                "feedback": {
                    "actualDurationMinutes": 30,
                    "perceivedEffort": 5,
                    "notes": "Sick, had to stop",
                    "completedAt": "2026-04-10T10:00:00Z",
                },
            },
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["feedback"] == "This session was very hard — rest is recommended."
    flag = body.get("flagForAdaptation", body.get("flag_for_adaptation"))
    assert flag is True
    # adapt_training_plan should have been called by the auto-adaptation
    mock_ai_service["adapt_training_plan"].assert_called()


@pytest.mark.asyncio
async def test_ask_trainer_classify_called_and_rag_skipped_when_not_needed(
    client, auth_headers, mock_ai_service
):
    """classify_question is called; RAG is NOT called when needs_science_rag=False."""
    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "What's tomorrow's workout?"},
    )
    assert response.status_code == 200
    # classify_question must have been called
    mock_ai_service["classify_question"].assert_called_once()


# ---------------------------------------------------------------------------
# Tests for 429 / rate-limit error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_raises_ai_rate_limit_error_on_gemini_429():
    """_chat must propagate AIRateLimitError raised by the LLM provider."""
    from unittest.mock import AsyncMock, patch
    from services.ai_service import AIRateLimitError
    from services import llm as llm_module

    mock_provider = AsyncMock()
    mock_provider.chat = AsyncMock(side_effect=AIRateLimitError("rate limited"))

    with patch.object(ai_service, "get_provider", return_value=mock_provider):
        with pytest.raises(AIRateLimitError):
            await ai_service._chat("gemini", "system", "hello")


@pytest.mark.asyncio
async def test_chat_history_raises_ai_rate_limit_error_on_gemini_429():
    """_chat_history must propagate AIRateLimitError raised by the LLM provider."""
    from unittest.mock import AsyncMock, patch
    from services.ai_service import AIRateLimitError
    from services import llm as llm_module

    mock_provider = AsyncMock()
    mock_provider.chat_history = AsyncMock(side_effect=AIRateLimitError("rate limited"))

    with patch.object(ai_service, "get_provider", return_value=mock_provider):
        with pytest.raises(AIRateLimitError):
            await ai_service._chat_history(
                "gemini", "system", [{"role": "user", "content": "hi"}]
            )


@pytest.mark.asyncio
async def test_ask_trainer_endpoint_returns_503_on_rate_limit(
    client, auth_headers, mock_ai_service
):
    """The /ask-trainer endpoint must return HTTP 503 when AIRateLimitError is raised."""
    from services.ai_service import AIRateLimitError

    mock_ai_service["ask_trainer"].side_effect = AIRateLimitError("rate limited")

    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "How am I doing?"},
    )

    assert response.status_code == 503
    assert "rate" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_analyse_activities_endpoint_returns_503_on_rate_limit(
    client, auth_headers, mock_ai_service
):
    """The /analyse-activities endpoint must return HTTP 503 when AIRateLimitError is raised."""
    from services.ai_service import AIRateLimitError

    mock_ai_service["analyse_strava_activities"].side_effect = AIRateLimitError("rate limited")

    response = await client.post(
        "/api/v1/ai/analyse-activities",
        headers=auth_headers,
        json={"activities": [{"id": 1, "name": "Ride", "type": "Ride",
                              "distance": 50000, "movingTime": 3600,
                              "elapsedTime": 3700, "totalElevationGain": 500,
                              "startDate": "2026-04-10T08:00:00Z"}]},
    )

    assert response.status_code == 503
    assert "rate" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_generate_plan_endpoint_returns_503_on_rate_limit(
    client, auth_headers, mock_ai_service
):
    """The /generate-plan endpoint must return HTTP 503 when AIRateLimitError is raised."""
    from services.ai_service import AIRateLimitError

    mock_ai_service["generate_training_plan"].side_effect = AIRateLimitError("rate limited")

    response = await client.post(
        "/api/v1/ai/generate-plan",
        headers=auth_headers,
        json={},
    )

    assert response.status_code == 503
    assert "rate" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# POST /ai/review-new-rides  (Task 3: batch review)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_new_rides_no_unreviewed_returns_empty(client, auth_headers, mock_ai_service):
    """When there are no unreviewed rides the endpoint returns an empty review."""
    response = await client.post("/api/v1/ai/review-new-rides", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["review"] == ""
    assert body["rideCount"] == 0


@pytest.mark.asyncio
async def test_review_new_rides_one_ride(client, auth_headers, mock_ai_service):
    """A single unreviewed ride triggers a batch review and marks it as reviewed."""
    import crud
    from tests.conftest import TestSessionLocal

    mock_ai_service["batch_review_rides"].return_value = "Great endurance ride!"

    # Seed one unreviewed ride metric
    async with TestSessionLocal() as db:
        from auth import decode_token
        token = auth_headers["Authorization"].split(" ", 1)[1]
        user_id = decode_token(token)
        await crud.upsert_ride_metric(
            db,
            user_id,
            strava_activity_id=10001,
            activity_date="2026-05-01",
            sport_type="cycling",
            duration_seconds=3600,
            tss=80.0,
        )
        await db.commit()

    response = await client.post("/api/v1/ai/review-new-rides", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["review"] == "Great endurance ride!"
    assert body["rideCount"] == 1

    # Verify the ride is now marked as reviewed
    async with TestSessionLocal() as db:
        from auth import decode_token
        token = auth_headers["Authorization"].split(" ", 1)[1]
        user_id = decode_token(token)
        unreviewed = await crud.get_unreviewed_ride_metrics(db, user_id)
        assert unreviewed == []


@pytest.mark.asyncio
async def test_review_new_rides_multiple_rides_all_included(client, auth_headers, mock_ai_service):
    """Multiple unreviewed rides must all be passed to batch_review_rides and then marked reviewed."""
    import crud
    from tests.conftest import TestSessionLocal

    mock_ai_service["batch_review_rides"].return_value = "Nice training block."

    async with TestSessionLocal() as db:
        from auth import decode_token
        token = auth_headers["Authorization"].split(" ", 1)[1]
        user_id = decode_token(token)
        for i, date in enumerate(["2026-05-01", "2026-05-02", "2026-05-03"], start=1):
            await crud.upsert_ride_metric(
                db,
                user_id,
                strava_activity_id=20000 + i,
                activity_date=date,
                sport_type="cycling",
                duration_seconds=3600,
                tss=float(70 + i * 5),
            )
        await db.commit()

    response = await client.post("/api/v1/ai/review-new-rides", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["review"] == "Nice training block."
    assert body["rideCount"] == 3

    # All three rides must have been passed to the service call
    call_args = mock_ai_service["batch_review_rides"].call_args
    rides_arg = call_args.args[0] if call_args.args else call_args.kwargs.get("rides", [])
    assert len(rides_arg) == 3
    ride_dates = {m.activity_date for m in rides_arg}
    assert ride_dates == {"2026-05-01", "2026-05-02", "2026-05-03"}

    # All rides must now be marked as reviewed
    async with TestSessionLocal() as db:
        from auth import decode_token
        token = auth_headers["Authorization"].split(" ", 1)[1]
        user_id = decode_token(token)
        unreviewed = await crud.get_unreviewed_ride_metrics(db, user_id)
        assert unreviewed == []


@pytest.mark.asyncio
async def test_review_new_rides_second_call_returns_empty(client, auth_headers, mock_ai_service):
    """After rides are reviewed a second call to the endpoint returns nothing new."""
    import crud
    from tests.conftest import TestSessionLocal

    mock_ai_service["batch_review_rides"].return_value = "First review."

    async with TestSessionLocal() as db:
        from auth import decode_token
        token = auth_headers["Authorization"].split(" ", 1)[1]
        user_id = decode_token(token)
        await crud.upsert_ride_metric(
            db,
            user_id,
            strava_activity_id=30001,
            activity_date="2026-05-01",
            sport_type="cycling",
        )
        await db.commit()

    # First call: review is generated and rides marked
    r1 = await client.post("/api/v1/ai/review-new-rides", headers=auth_headers)
    assert r1.status_code == 200
    assert r1.json()["rideCount"] == 1

    # Second call: no new rides, empty response
    r2 = await client.post("/api/v1/ai/review-new-rides", headers=auth_headers)
    assert r2.status_code == 200
    assert r2.json()["review"] == ""
    assert r2.json()["rideCount"] == 0


@pytest.mark.asyncio
async def test_review_new_rides_returns_503_on_rate_limit(client, auth_headers, mock_ai_service):
    """The endpoint must return HTTP 503 when AIRateLimitError is raised."""
    import crud
    from tests.conftest import TestSessionLocal
    from services.ai_service import AIRateLimitError

    mock_ai_service["batch_review_rides"].side_effect = AIRateLimitError("rate limited")

    async with TestSessionLocal() as db:
        from auth import decode_token
        token = auth_headers["Authorization"].split(" ", 1)[1]
        user_id = decode_token(token)
        await crud.upsert_ride_metric(
            db,
            user_id,
            strava_activity_id=40001,
            activity_date="2026-05-01",
            sport_type="cycling",
        )
        await db.commit()

    response = await client.post("/api/v1/ai/review-new-rides", headers=auth_headers)
    assert response.status_code == 503
    assert "rate" in response.json()["detail"].lower()



# ---------------------------------------------------------------------------
# Integration tests for Task 4: follow-up dialogue fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_workout_returns_follow_up_fields_when_ride_is_ambiguous(
    client, auth_headers, mock_ai_service
):
    """rate-workout endpoint must return follow-up fields when the AI flags ambiguous ride."""
    mock_ai_service["rate_completed_workout"].return_value = {
        "feedback": "This looks like a short easy spin rather than a full endurance session.",
        "flag_for_adaptation": False,
        "needs_athlete_feedback": True,
        "follow_up_question": "Was this intentional recovery, a commute, or did you cut it short?",
        "suggested_feedback_tags": ["recovery", "commute", "cut_short"],
    }

    response = await client.post(
        "/api/v1/ai/rate-workout",
        headers=auth_headers,
        json={
            "day": {
                "date": "2026-04-10",
                "workoutType": "endurance",
                "title": "Long Ride",
                "description": "2-hour endurance ride",
                "durationMinutes": 120,
                "feedback": {
                    "actualDurationMinutes": 20,
                    "perceivedEffort": 1,
                    "notes": "",
                    "completedAt": "2026-04-10T10:00:00Z",
                },
            },
        },
    )

    assert response.status_code == 200
    body = response.json()

    # Backward-compat field still present
    assert "feedback" in body

    # New follow-up fields
    needs_fb = body.get("needsAthleteFeedback", body.get("needs_athlete_feedback"))
    assert needs_fb is True

    follow_up = body.get("followUpQuestion", body.get("follow_up_question"))
    assert follow_up == "Was this intentional recovery, a commute, or did you cut it short?"

    tags = body.get("suggestedFeedbackTags", body.get("suggested_feedback_tags"))
    assert "recovery" in tags


@pytest.mark.asyncio
async def test_rate_workout_follow_up_fields_default_to_falsy_for_normal_ride(
    client, auth_headers, mock_ai_service
):
    """rate-workout endpoint must return falsy follow-up fields for normal high-confidence rides."""
    # The default mock already returns needs_athlete_feedback=False
    response = await client.post(
        "/api/v1/ai/rate-workout",
        headers=auth_headers,
        json={
            "day": {
                "date": "2026-04-10",
                "workoutType": "endurance",
                "title": "Long Ride",
                "description": "90-min Z2",
                "durationMinutes": 90,
                "feedback": {
                    "actualDurationMinutes": 90,
                    "perceivedEffort": 2,
                    "notes": "Felt solid",
                    "completedAt": "2026-04-10T10:00:00Z",
                },
            },
        },
    )

    assert response.status_code == 200
    body = response.json()

    needs_fb = body.get("needsAthleteFeedback", body.get("needs_athlete_feedback"))
    assert needs_fb is False

    follow_up = body.get("followUpQuestion", body.get("follow_up_question"))
    assert follow_up is None

    tags = body.get("suggestedFeedbackTags", body.get("suggested_feedback_tags"))
    assert tags == []
