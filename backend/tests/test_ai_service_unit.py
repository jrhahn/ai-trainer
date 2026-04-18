"""Unit tests for services/ai_service.py helper functions."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

import services.ai_service as ai_service
import services.analysis as analysis


# ---------------------------------------------------------------------------
# _parse_ai_json
# ---------------------------------------------------------------------------


def test_parse_ai_json_raw_json():
    raw = '{"estimatedFTP": 300, "riderType": "allrounder"}'
    result = ai_service._parse_ai_json(raw)
    assert result["estimatedFTP"] == 300
    assert result["riderType"] == "allrounder"


def test_parse_ai_json_fenced_code_block():
    raw = '```json\n{"estimatedFTP": 280, "riderType": "climber"}\n```'
    result = ai_service._parse_ai_json(raw)
    assert result["estimatedFTP"] == 280
    assert result["riderType"] == "climber"


def test_parse_ai_json_fenced_block_without_language():
    raw = '```\n{"plan": [{"date": "2026-04-10"}]}\n```'
    result = ai_service._parse_ai_json(raw)
    assert result["plan"][0]["date"] == "2026-04-10"


def test_parse_ai_json_repairs_trailing_comma():
    raw = '{"key": "value",}'
    result = ai_service._parse_ai_json(raw)
    assert result["key"] == "value"


# ---------------------------------------------------------------------------
# _best_n_min_power
# ---------------------------------------------------------------------------


def test_best_n_min_power_empty():
    result, s, e = analysis._best_n_min_power([], [], 20)
    assert result is None
    assert s == 0 and e == 0


def test_best_n_min_power_mismatched_lengths():
    result, s, e = analysis._best_n_min_power([200.0, 210.0], [0], 20)
    assert result is None


def test_best_n_min_power_window_too_short():
    # Only 5 seconds of data — can't fill a 20-min window (1200 s × 90% = 1080 s needed)
    watts = [300.0] * 5
    time_stream = list(range(5))
    result, s, e = analysis._best_n_min_power(watts, time_stream, 20)
    assert result is None


def test_best_n_min_power_exact_window():
    # 20 minutes of data at constant 300 W — best 20-min should equal 300
    watts = [300.0] * 1200
    time_stream = list(range(1200))
    result, s, e = analysis._best_n_min_power(watts, time_stream, 20)
    assert result is not None
    assert abs(result - 300.0) < 1.0


def test_best_n_min_power_finds_peak():
    # Mostly 200 W but one 20-min block at 300 W in the middle
    baseline = [200.0] * 600
    peak = [300.0] * 1200
    rest = [200.0] * 600
    watts = baseline + peak + rest
    time_stream = list(range(len(watts)))
    result, s, e = analysis._best_n_min_power(watts, time_stream, 20)
    assert result is not None
    assert result >= 290.0  # should be close to the peak block


# ---------------------------------------------------------------------------
# _hr_corrected_ftp
# ---------------------------------------------------------------------------


def test_hr_corrected_ftp_zero_hr():
    assert analysis._hr_corrected_ftp(300.0, 0.0, 190) is None


def test_hr_corrected_ftp_zero_max_hr():
    assert analysis._hr_corrected_ftp(300.0, 160.0, 0) is None


def test_hr_corrected_ftp_below_70pct():
    # interval_hr = 100 bpm, max_hr = 190 → hr_fraction = 0.526 → < 0.70
    assert analysis._hr_corrected_ftp(300.0, 100.0, 190) is None


def test_hr_corrected_ftp_above_100pct():
    # interval_hr > max_hr → hr_fraction > 1.0
    assert analysis._hr_corrected_ftp(300.0, 200.0, 190) is None


def test_hr_corrected_ftp_at_lthr():
    # interval_hr exactly at LTHR (0.87 × max_hr) → correction factor = 1.0
    max_hr = 190
    lthr = round(max_hr * analysis._LTHR_RATIO)
    result = analysis._hr_corrected_ftp(300.0, float(lthr), max_hr)
    assert result is not None
    assert abs(result - 300) <= 2  # should be ~300 W


def test_hr_corrected_ftp_below_lthr():
    # Interval HR below LTHR → rider had headroom → corrected FTP > interval power
    result = analysis._hr_corrected_ftp(280.0, 150.0, 190)
    assert result is not None
    assert result > 280


# ---------------------------------------------------------------------------
# _compute_hr_zones
# ---------------------------------------------------------------------------


def test_compute_hr_zones_structure():
    zones = analysis._compute_hr_zones(200)
    assert set(zones.keys()) == {"zone1", "zone2", "zone3", "zone4", "zone5"}
    for zone in zones.values():
        assert "low" in zone and "high" in zone


def test_compute_hr_zones_boundaries():
    max_hr = 200
    zones = analysis._compute_hr_zones(max_hr)
    # Zone 1 low should be 0, zone 5 high should be max_hr
    assert zones["zone1"]["low"] == 0
    assert zones["zone5"]["high"] == max_hr
    # Zones should be monotonically increasing
    boundaries = [
        zones["zone1"]["high"],
        zones["zone2"]["high"],
        zones["zone3"]["high"],
        zones["zone4"]["high"],
        zones["zone5"]["high"],
    ]
    assert boundaries == sorted(boundaries)


def test_compute_hr_zones_values():
    zones = analysis._compute_hr_zones(180)
    assert zones["zone1"]["high"] == round(180 * 0.60)
    assert zones["zone2"]["low"] == round(180 * 0.60)
    assert zones["zone2"]["high"] == round(180 * 0.70)
    assert zones["zone5"]["low"] == round(180 * 0.90)


# ---------------------------------------------------------------------------
# _classify_ride_purpose — additional branches
# ---------------------------------------------------------------------------


def test_classify_ride_purpose_empty_watts():
    # Should not raise; returns "endurance" as a safe default
    result = analysis._classify_ride_purpose([], [], 250)
    assert result == "endurance"


def test_classify_ride_purpose_zero_ftp():
    watts = [200.0] * 600
    ts = list(range(600))
    result = analysis._classify_ride_purpose(watts, ts, 0)
    assert result == "endurance"


def test_classify_ride_purpose_sweetspot():
    """10-20 min efforts at 88-95% FTP should be classified as sweetspot."""
    ftp = 250
    work = round(ftp * 0.91)  # 91% FTP — sweetspot
    rest = round(ftp * 0.50)
    watts: list[float] = []
    # 3 × 15 min at sweetspot
    for _ in range(3):
        watts.extend([work] * 900)  # 15 min
        watts.extend([rest] * 300)  # 5 min rest
    ts = list(range(len(watts)))
    result = analysis._classify_ride_purpose(watts, ts, ftp)
    assert result == "interval_sweetspot"


def test_classify_ride_purpose_threshold():
    """5-12 min efforts at 95-105% FTP should be classified as threshold."""
    ftp = 250
    work = round(ftp * 1.00)  # exactly 100% FTP
    rest = round(ftp * 0.50)
    watts: list[float] = []
    # 4 × 8 min at threshold
    for _ in range(4):
        watts.extend([work] * 480)  # 8 min
        watts.extend([rest] * 240)  # 4 min rest
    ts = list(range(len(watts)))
    result = analysis._classify_ride_purpose(watts, ts, ftp)
    assert result == "interval_threshold"


def test_classify_ride_purpose_mixed():
    """A ride with both VO2max and sprint intervals should be 'mixed'."""
    ftp = 250
    vo2_power = round(ftp * 1.15)
    sprint_power = round(ftp * 1.45)
    rest = round(ftp * 0.40)
    watts: list[float] = []
    # 3 × 4 min VO2max
    for _ in range(3):
        watts.extend([vo2_power] * 240)
        watts.extend([rest] * 300)
    # 4 × 90 s sprints (> min_interval_secs=30, duration < 2 min → sprint category)
    for _ in range(4):
        watts.extend([sprint_power] * 90)
        watts.extend([rest] * 240)
    ts = list(range(len(watts)))
    result = analysis._classify_ride_purpose(watts, ts, ftp)
    assert result == "mixed"


def test_classify_ride_purpose_intervals_below_thresholds():
    """Short catch-all efforts (< 2 min, power 85-130% FTP) are classified as 'interval_sprints'."""
    ftp = 250
    # Power at exactly 86% FTP — above detection threshold (85%) but below the
    # primary sprint threshold (130%). Duration 60 s (< 2 min) routes to
    # the catch-all sprint branch in _classify_ride_purpose.
    work = round(ftp * 0.86)
    rest = round(ftp * 0.40)
    watts: list[float] = []
    for _ in range(5):
        watts.extend([work] * 60)   # 60 s effort (< 2 min)
        watts.extend([rest] * 120)  # 2 min rest
    ts = list(range(len(watts)))
    result = analysis._classify_ride_purpose(watts, ts, ftp)
    # Catch-all branch: pct=0.86>0.85 and dur_min<2 → "sprint" → "interval_sprints"
    assert result == "interval_sprints"


# ---------------------------------------------------------------------------
# _detect_intervals — edge cases
# ---------------------------------------------------------------------------


def test_detect_intervals_empty():
    assert analysis._detect_intervals([], [], 250) == []


def test_detect_intervals_zero_ftp():
    watts = [300.0] * 600
    ts = list(range(600))
    assert analysis._detect_intervals(watts, ts, 0) == []


def test_detect_intervals_merges_short_gaps():
    """Two intervals separated by a brief recovery should be merged."""
    ftp = 250
    work = round(ftp * 1.10)
    rest = round(ftp * 0.40)
    # 5 min work, 20 s rest (< recovery_gap_secs=30), 5 min work
    watts: list[float] = [work] * 300 + [rest] * 20 + [work] * 300
    ts = list(range(len(watts)))
    intervals = analysis._detect_intervals(watts, ts, ftp)
    # The two efforts should be merged into one
    assert len(intervals) == 1


def test_detect_intervals_ends_in_high_power():
    """Interval block that ends at the last sample should still be detected."""
    ftp = 250
    work = round(ftp * 1.10)
    rest = round(ftp * 0.40)
    watts: list[float] = [rest] * 300 + [work] * 300
    ts = list(range(len(watts)))
    intervals = analysis._detect_intervals(watts, ts, ftp)
    assert len(intervals) == 1
    assert intervals[0]["duration_secs"] >= 270


# ---------------------------------------------------------------------------
# _build_ride_analysis — edge cases
# ---------------------------------------------------------------------------


def test_build_ride_analysis_empty_streams():
    result = analysis._build_ride_analysis({}, 250.0)
    assert result == {}


def test_build_ride_analysis_no_time_data():
    streams = {"watts": {"data": [200.0] * 600}}
    result = analysis._build_ride_analysis(streams, 250.0)
    assert result == {}


def test_build_ride_analysis_no_hr_data():
    """Without HR data the function should still return a result."""
    ftp = 250.0
    watts = [ftp * 0.70] * 3600
    ts = list(range(3600))
    streams = {"watts": {"data": watts}, "time": {"data": ts}}
    result = analysis._build_ride_analysis(streams, ftp)
    assert "ride_category" in result
    assert result["ride_category"] == "endurance"


# ---------------------------------------------------------------------------
# _compute_hr_drift — short segment
# ---------------------------------------------------------------------------


def test_compute_hr_drift_too_short():
    assert analysis._compute_hr_drift([150.0, 155.0]) is None


def test_compute_hr_drift_flat():
    # Exactly flat HR — slope should be ~0
    hr = [150.0] * 100
    drift = analysis._compute_hr_drift(hr)
    assert drift is not None
    assert abs(drift) < 0.001


# ---------------------------------------------------------------------------
# analyse_strava_activities with mocked _chat
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyse_strava_activities_with_streams():
    """analyse_strava_activities should override AI FTP with algorithmic value."""
    ftp = 280
    # Build a stream that gives a predictable 20-min best power
    work_power = round(ftp / 0.95)  # so that 0.95 × work_power ≈ ftp
    watts = [float(work_power)] * 1500  # 25 min @ work_power
    time_stream = list(range(1500))

    streams_by_id = {
        "42": {
            "watts": {"data": watts},
            "time": {"data": time_stream},
        }
    }
    activities = [
        {
            "id": 42,
            "name": "Test Ride",
            "type": "Ride",
            "distance": 40000,
            "movingTime": 1500,
            "elapsedTime": 1500,
            "totalElevationGain": 200,
            "startDate": "2026-04-10T08:00:00Z",
            "averageWatts": work_power,
        }
    ]

    ai_response = json.dumps(
        {
            "estimatedFTP": 999,  # AI guess — should be overridden
            "estimatedThresholdHR": None,
            "riderType": "allrounder",
            "notes": "Looks good.",
            "rideInsights": "Solid endurance ride.",
            "lastRideFeedback": "Nice effort today.",
        }
    )

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False):
        return ai_response

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        result = await ai_service.analyse_strava_activities(
            activities, provider="openai", streams_by_id=streams_by_id
        )

    # Algorithmic FTP should override the AI's guess
    assert result["estimatedFTP"] != 999
    assert result["estimatedFTP"] is not None
    assert result["riderType"] == "allrounder"


@pytest.mark.asyncio
async def test_analyse_strava_activities_with_max_hr_sets_hr_zones():
    """When max_heart_rate is provided, hrZones should be computed."""
    activities = [
        {
            "id": 1,
            "name": "Easy Ride",
            "type": "Ride",
            "distance": 30000,
            "movingTime": 3600,
            "elapsedTime": 3600,
            "totalElevationGain": 100,
            "startDate": "2026-04-10T08:00:00Z",
            "averageWatts": 180,
        }
    ]

    ai_response = json.dumps(
        {
            "estimatedFTP": 250,
            "estimatedThresholdHR": 165,
            "riderType": "endurance",
            "notes": "Good aerobic base.",
            "rideInsights": "Endurance ride.",
            "lastRideFeedback": "Solid session.",
        }
    )

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False):
        return ai_response

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        result = await ai_service.analyse_strava_activities(
            activities, provider="openai", max_heart_rate=190
        )

    assert "hrZones" in result
    assert result["hrZones"] is not None
    assert "zone1" in result["hrZones"]
    assert "zone5" in result["hrZones"]


@pytest.mark.asyncio
async def test_analyse_strava_activities_no_streams():
    """analyse_strava_activities works without stream data (summary-only)."""
    activities = [
        {
            "id": 10,
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

    ai_response = json.dumps(
        {
            "estimatedFTP": 290,
            "estimatedThresholdHR": None,
            "riderType": "allrounder",
            "notes": "Good effort.",
            "rideInsights": "Solid ride.",
            "lastRideFeedback": "Nice work.",
        }
    )

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False):
        return ai_response

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        result = await ai_service.analyse_strava_activities(activities, provider="openai")

    assert result["estimatedFTP"] == 290


@pytest.mark.asyncio
async def test_generate_training_plan_calls_chat():
    """generate_training_plan should return the plan list from AI response."""
    profile = {
        "bikeType": "road",
        "trainingGoal": "ftp_improvement",
        "fitnessLevel": "intermediate",
    }
    fake_plan = [
        {
            "date": "2026-04-10",
            "workoutType": "endurance",
            "title": "Endurance",
            "description": "Long ride",
            "durationMinutes": 90,
        }
    ]

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False):
        return json.dumps({"plan": fake_plan})

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        result = await ai_service.generate_training_plan(profile, provider="openai")

    assert result == fake_plan


@pytest.mark.asyncio
async def test_adapt_training_plan_calls_chat():
    """adapt_training_plan should merge AI updated days into the plan."""
    plan = [
        {
            "date": "2026-04-10",
            "workoutType": "endurance",
            "title": "Endurance",
            "description": "Long ride",
            "durationMinutes": 90,
            "completed": False,
        }
    ]
    recent_feedback = [
        {
            "actualDurationMinutes": 60,
            "perceivedEffort": 5,
            "notes": "Very hard",
            "completedAt": "2026-04-09T10:00:00Z",
        }
    ]
    profile = {"fitnessLevel": "intermediate"}
    updated_day = {
        "date": "2026-04-10",
        "workoutType": "recovery",
        "title": "Recovery",
        "description": "Easy spin",
        "durationMinutes": 45,
    }

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False):
        return json.dumps({"updatedDays": [updated_day]})

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        result = await ai_service.adapt_training_plan(
            plan, recent_feedback, profile, provider="openai"
        )

    assert result[0]["workoutType"] == "recovery"


@pytest.mark.asyncio
async def test_update_coach_memory_calls_chat():
    async def fake_chat(provider, system_prompt, user_msg, json_mode=False):
        return "Prefers morning rides. FTP ~280 W."

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        result = await ai_service.update_coach_memory(
            current_memory="",
            user_message="I always ride in the morning.",
            coach_response="Got it, I'll schedule morning sessions.",
            provider="openai",
        )

    assert "morning" in result.lower() or "280" in result


@pytest.mark.asyncio
async def test_rate_completed_workout_no_feedback():
    """rate_completed_workout returns empty feedback dict when feedback is missing."""
    result = await ai_service.rate_completed_workout(
        day={"workoutType": "endurance", "title": "Ride", "description": "...", "durationMinutes": 60},
        profile={"fitnessLevel": "intermediate"},
        provider="openai",
    )
    assert result == {"feedback": "", "flag_for_adaptation": False}


@pytest.mark.asyncio
async def test_rate_completed_workout_calls_chat():
    day = {
        "workoutType": "intervals",
        "title": "VO2 Max",
        "description": "Hard reps",
        "durationMinutes": 60,
        "targetPower": {"low": 300, "high": 340},
        "targetHeartRate": {"low": 160, "high": 175},
        "feedback": {
            "actualDurationMinutes": 58,
            "perceivedEffort": 4,
            "averagePower": 310,
            "peakPower": 450,
            "averageHeartRate": 168,
            "notes": "Tough but done",
            "completedAt": "2026-04-10T10:00:00Z",
        },
    }
    profile = {"fitnessLevel": "advanced"}

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False):
        return json.dumps({"feedback": "Great effort today! You nailed the power targets.", "flag_for_adaptation": False})

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        result = await ai_service.rate_completed_workout(day, profile, provider="openai")

    assert isinstance(result, dict)
    assert "effort" in result["feedback"].lower() or "power" in result["feedback"].lower()
    assert result["flag_for_adaptation"] is False


@pytest.mark.asyncio
async def test_analyse_strava_activities_with_streams_and_hr():
    """HR-corrected FTP should be computed when max_heart_rate and HR streams are present."""
    ftp = 280
    work_power = round(ftp / 0.95)
    watts = [float(work_power)] * 1500
    time_stream = list(range(1500))
    # HR data: constant at LTHR (87% of max_hr=190 → 165 bpm)
    max_hr = 190
    hr = [165.0] * 1500

    streams_by_id = {
        "99": {
            "watts": {"data": watts},
            "heartrate": {"data": hr},
            "time": {"data": time_stream},
        }
    }
    activities = [
        {
            "id": 99,
            "name": "Test Ride",
            "type": "Ride",
            "distance": 40000,
            "movingTime": 1500,
            "elapsedTime": 1500,
            "totalElevationGain": 200,
            "startDate": "2026-04-10T08:00:00Z",
            "averageWatts": work_power,
        }
    ]

    ai_response = json.dumps(
        {
            "estimatedFTP": 999,
            "estimatedThresholdHR": 999,
            "riderType": "allrounder",
            "notes": "Good.",
            "rideInsights": "Solid.",
            "lastRideFeedback": "Nice.",
        }
    )

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False):
        return ai_response

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        result = await ai_service.analyse_strava_activities(
            activities,
            provider="openai",
            streams_by_id=streams_by_id,
            max_heart_rate=max_hr,
        )

    # Algorithmic FTP from stream data should override the AI response
    assert result["estimatedFTP"] is not None
    assert result["estimatedFTP"] != 999
    # HR zones should be populated
    assert result.get("hrZones") is not None


# ---------------------------------------------------------------------------
# _compute_training_load (Task 1)
# ---------------------------------------------------------------------------


def test_compute_training_load_empty():
    result = analysis._compute_training_load([], 250.0)
    assert result == {"ctl": 0.0, "atl": 0.0, "tsb": 0.0, "daily_tss": []}


def test_compute_training_load_zero_ftp():
    plan = [{"durationMinutes": 60, "workoutType": "endurance"}]
    result = analysis._compute_training_load(plan, 0.0)
    assert result == {"ctl": 0.0, "atl": 0.0, "tsb": 0.0, "daily_tss": []}


def test_compute_training_load_returns_expected_keys():
    plan = [
        {"durationMinutes": 90, "workoutType": "endurance"},
        {"durationMinutes": 0, "workoutType": "rest"},
        {"durationMinutes": 60, "workoutType": "intervals", "targetPower": {"low": 280, "high": 320}},
    ]
    result = analysis._compute_training_load(plan, 300.0)
    assert "ctl" in result and "atl" in result and "tsb" in result and "daily_tss" in result
    assert len(result["daily_tss"]) == 3
    # Rest day should produce 0 TSS
    assert result["daily_tss"][1] == 0.0
    # TSB = CTL - ATL (may differ by up to 0.2 due to independent rounding)
    assert abs(result["tsb"] - (result["ctl"] - result["atl"])) < 0.2


def test_compute_training_load_target_power():
    """When targetPower is given, TSS should use mid-point for IF calculation."""
    plan = [{"durationMinutes": 60, "workoutType": "endurance", "targetPower": {"low": 240, "high": 260}}]
    result = analysis._compute_training_load(plan, 250.0)
    # mid-point = 250W, FTP = 250W → IF = 1.0, TSS = (3600 × 250 × 1) / (250 × 3600) × 100 = 100
    assert abs(result["daily_tss"][0] - 100.0) < 1.0


def test_compute_training_load_tsb_equals_ctl_minus_atl():
    plan = [{"durationMinutes": 60, "workoutType": "tempo"}] * 10
    result = analysis._compute_training_load(plan, 250.0)
    # TSB = CTL - ATL (may differ by up to 0.2 due to independent rounding of each value)
    assert abs(result["tsb"] - (result["ctl"] - result["atl"])) < 0.2


# ---------------------------------------------------------------------------
# ask_trainer — thinking stripped, classification mock (Tasks 2 & 5)
# ---------------------------------------------------------------------------

PLAN_FOR_LOAD_TESTS = [
    {
        "date": "2026-04-16",
        "workoutType": "intervals",
        "title": "VO2 Intervals",
        "description": "5×4 min at 110% FTP",
        "durationMinutes": 60,
        "targetPower": {"low": 280, "high": 320},
    }
]

PROFILE_WITH_FTP = {
    "name": "Test Rider",
    "email": "rider@example.com",
    "bikeType": "road",
    "trainingGoal": "ftp_improvement",
    "fitnessLevel": "intermediate",
    "currentFTP": 300,
}


@pytest.mark.asyncio
async def test_ask_trainer_thinking_not_in_return():
    """'thinking' must be stripped from the return value and never reach the frontend."""

    async def fake_chat_history(provider, system_prompt, messages, json_mode=False):
        return json.dumps({
            "thinking": "The athlete is asking about tomorrow's workout...",
            "response": "Tomorrow is an endurance ride.",
            "planUpdates": [],
            "sources": [],
        })

    with patch.object(ai_service, "_chat_history", side_effect=fake_chat_history):
        result = await ai_service.ask_trainer(
            question="What's tomorrow's workout?",
            plan=PLAN_FOR_LOAD_TESTS,
            profile=PROFILE_WITH_FTP,
        )

    assert "thinking" not in result
    assert result["response"] == "Tomorrow is an endurance ride."


@pytest.mark.asyncio
async def test_ask_trainer_classify_step_skips_rag_when_not_needed():
    """When classify says needs_science_rag=False, the effective science context is empty."""
    captured_system_prompt: list[str] = []

    async def fake_classify(system_prompt, user_msg, json_mode=False):
        return json.dumps({"category": "plan_query", "needs_science_rag": False})

    async def fake_chat_history(provider, system_prompt, messages, json_mode=False):
        captured_system_prompt.append(system_prompt)
        return json.dumps({"response": "Here's your plan.", "planUpdates": [], "sources": []})

    with (
        patch.object(ai_service, "_openai_chat", side_effect=fake_classify),
        patch.object(ai_service, "_chat_history", side_effect=fake_chat_history),
    ):
        result = await ai_service.ask_trainer(
            question="What's tomorrow's workout?",
            plan=PLAN_FOR_LOAD_TESTS,
            profile=PROFILE_WITH_FTP,
            # No science_context provided; should not be in the prompt
        )

    assert result["response"] == "Here's your plan."
    # No science research section should appear in the prompt
    assert "Relevant cycling science research" not in captured_system_prompt[0]


@pytest.mark.asyncio
async def test_ask_trainer_training_load_in_prompt():
    """CTL/ATL/TSB should appear in the system prompt when FTP is known."""
    captured_prompt: list[str] = []

    async def fake_classify(system_prompt, user_msg, json_mode=False):
        return json.dumps({"category": "plan_query", "needs_science_rag": False})

    async def fake_chat_history(provider, system_prompt, messages, json_mode=False):
        captured_prompt.append(system_prompt)
        return json.dumps({"response": "OK.", "planUpdates": [], "sources": []})

    with (
        patch.object(ai_service, "_openai_chat", side_effect=fake_classify),
        patch.object(ai_service, "_chat_history", side_effect=fake_chat_history),
    ):
        await ai_service.ask_trainer(
            question="Am I too tired?",
            plan=PLAN_FOR_LOAD_TESTS,
            profile=PROFILE_WITH_FTP,
        )

    prompt = captured_prompt[0]
    assert "CTL" in prompt
    assert "ATL" in prompt
    assert "TSB" in prompt


# ---------------------------------------------------------------------------
# rate_completed_workout — structured output, flag_for_adaptation (Task 4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_completed_workout_returns_dict():
    """rate_completed_workout must return a dict with feedback and flag_for_adaptation."""
    day = {
        "workoutType": "intervals",
        "title": "VO2",
        "description": "Hard",
        "durationMinutes": 60,
        "feedback": {
            "actualDurationMinutes": 30,
            "perceivedEffort": 5,
            "notes": "Felt terrible, might be sick",
            "completedAt": "2026-04-10T10:00:00Z",
        },
    }

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False):
        return json.dumps({"feedback": "Tough session.", "flag_for_adaptation": True})

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        result = await ai_service.rate_completed_workout(day, {}, provider="openai")

    assert isinstance(result, dict)
    assert result["feedback"] == "Tough session."
    assert result["flag_for_adaptation"] is True


@pytest.mark.asyncio
async def test_rate_completed_workout_flag_false_for_normal_session():
    day = {
        "workoutType": "endurance",
        "title": "Easy Ride",
        "description": "Easy",
        "durationMinutes": 90,
        "feedback": {
            "actualDurationMinutes": 90,
            "perceivedEffort": 2,
            "completedAt": "2026-04-10T10:00:00Z",
        },
    }

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False):
        return json.dumps({"feedback": "Great session, well done!", "flag_for_adaptation": False})

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        result = await ai_service.rate_completed_workout(day, {}, provider="openai")

    assert result["flag_for_adaptation"] is False


# ---------------------------------------------------------------------------
# adapt_plan_system includes TRAINING_PLAN_PRINCIPLES (Task 3)
# ---------------------------------------------------------------------------


def test_adapt_plan_system_includes_training_principles():
    """adapt_plan_system must embed TRAINING_PLAN_PRINCIPLES like generate_plan_system does."""
    from services.prompts import adapt_plan_system, TRAINING_PLAN_PRINCIPLES
    system = adapt_plan_system()
    # A key rule from TRAINING_PLAN_PRINCIPLES
    assert "Never schedule two hard days back-to-back" in system or "back-to-back" in system


def test_adapt_plan_system_includes_tsb_guidance():
    from services.prompts import adapt_plan_system
    system = adapt_plan_system()
    assert "TSB" in system
    assert "fatigue" in system.lower() or "recovery" in system.lower()


# ---------------------------------------------------------------------------
# classify_question (Task 5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_question_returns_default_on_failure():
    """classify_question must return a safe default if the AI call fails."""
    async def broken_chat(system_prompt, user_msg, json_mode=False):
        raise RuntimeError("API unavailable")

    with patch.object(ai_service, "_openai_chat", side_effect=broken_chat):
        result = await ai_service.classify_question("What's my FTP?")

    assert result["category"] == "general_coaching"
    assert result["needs_science_rag"] is False


@pytest.mark.asyncio
async def test_classify_question_parses_response():
    async def fake_chat(system_prompt, user_msg, json_mode=False):
        return json.dumps({"category": "science_question", "needs_science_rag": True})

    with patch.object(ai_service, "_openai_chat", side_effect=fake_chat):
        result = await ai_service.classify_question("How does VO2max training work?")

    assert result["category"] == "science_question"
    assert result["needs_science_rag"] is True
