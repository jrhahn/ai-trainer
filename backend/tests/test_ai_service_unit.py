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
    result, s, e = analysis.best_n_min_power([], [], 20)
    assert result is None
    assert s == 0 and e == 0


def test_best_n_min_power_mismatched_lengths():
    result, s, e = analysis.best_n_min_power([200.0, 210.0], [0], 20)
    assert result is None


def test_best_n_min_power_window_too_short():
    # Only 5 seconds of data — can't fill a 20-min window (1200 s × 90% = 1080 s needed)
    watts = [300.0] * 5
    time_stream = list(range(5))
    result, s, e = analysis.best_n_min_power(watts, time_stream, 20)
    assert result is None


def test_best_n_min_power_exact_window():
    # 20 minutes of data at constant 300 W — best 20-min should equal 300
    watts = [300.0] * 1200
    time_stream = list(range(1200))
    result, s, e = analysis.best_n_min_power(watts, time_stream, 20)
    assert result is not None
    assert abs(result - 300.0) < 1.0


def test_best_n_min_power_finds_peak():
    # Mostly 200 W but one 20-min block at 300 W in the middle
    baseline = [200.0] * 600
    peak = [300.0] * 1200
    rest = [200.0] * 600
    watts = baseline + peak + rest
    time_stream = list(range(len(watts)))
    result, s, e = analysis.best_n_min_power(watts, time_stream, 20)
    assert result is not None
    assert result >= 290.0  # should be close to the peak block


# ---------------------------------------------------------------------------
# _hr_corrected_ftp
# ---------------------------------------------------------------------------


def test_hr_corrected_ftp_zero_hr():
    assert analysis.hr_corrected_ftp(300.0, 0.0, 190) is None


def test_hr_corrected_ftp_zero_max_hr():
    assert analysis.hr_corrected_ftp(300.0, 160.0, 0) is None


def test_hr_corrected_ftp_below_70pct():
    # interval_hr = 100 bpm, max_hr = 190 → hr_fraction = 0.526 → < 0.70
    assert analysis.hr_corrected_ftp(300.0, 100.0, 190) is None


def test_hr_corrected_ftp_above_100pct():
    # interval_hr > max_hr → hr_fraction > 1.0
    assert analysis.hr_corrected_ftp(300.0, 200.0, 190) is None


def test_hr_corrected_ftp_at_lthr():
    # interval_hr exactly at LTHR (0.87 × max_hr) → correction factor = 1.0
    max_hr = 190
    lthr = round(max_hr * analysis.LTHR_RATIO)
    result = analysis.hr_corrected_ftp(300.0, float(lthr), max_hr)
    assert result is not None
    assert abs(result - 300) <= 2  # should be ~300 W


def test_hr_corrected_ftp_below_lthr():
    # Interval HR below LTHR → rider had headroom → corrected FTP > interval power
    result = analysis.hr_corrected_ftp(280.0, 150.0, 190)
    assert result is not None
    assert result > 280


# ---------------------------------------------------------------------------
# _compute_hr_zones
# ---------------------------------------------------------------------------


def test_compute_hr_zones_structure():
    zones = analysis.compute_hr_zones(200)
    assert set(zones.keys()) == {"zone1", "zone2", "zone3", "zone4", "zone5"}
    for zone in zones.values():
        assert "low" in zone and "high" in zone


def test_compute_hr_zones_boundaries():
    max_hr = 200
    zones = analysis.compute_hr_zones(max_hr)
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
    zones = analysis.compute_hr_zones(180)
    assert zones["zone1"]["high"] == round(180 * 0.60)
    assert zones["zone2"]["low"] == round(180 * 0.60)
    assert zones["zone2"]["high"] == round(180 * 0.70)
    assert zones["zone5"]["low"] == round(180 * 0.90)


# ---------------------------------------------------------------------------
# _classify_ride_purpose — additional branches
# ---------------------------------------------------------------------------


def test_classify_ride_purpose_empty_watts():
    # Should not raise; insufficient data should not be treated as endurance.
    result = analysis.classify_ride_purpose([], [], 250)
    assert result == "unknown"


def test_classify_ride_purpose_zero_ftp():
    watts = [200.0] * 600
    ts = list(range(600))
    result = analysis.classify_ride_purpose(watts, ts, 0)
    assert result == "unknown"


def test_classify_ride_purpose_missing_watts_with_duration():
    ts = list(range(5 * 60))
    result = analysis.classify_ride_purpose([], ts, 250)
    assert result == "unknown"


def test_classify_ride_purpose_short_easy_spin():
    ftp = 250
    watts = [round(ftp * 0.68)] * (15 * 60)
    ts = list(range(len(watts)))
    result = analysis.classify_ride_purpose(watts, ts, ftp)
    assert result == "short_easy_spin"


def test_classify_ride_purpose_short_hard_effort():
    ftp = 250
    watts = [round(ftp * 0.80)] * (20 * 60)
    ts = list(range(len(watts)))
    result = analysis.classify_ride_purpose(watts, ts, ftp)
    assert result == "short_hard_effort"


def test_classify_ride_purpose_long_z2_still_endurance():
    ftp = 250
    watts = [round(ftp * 0.68)] * (60 * 60)
    ts = list(range(len(watts)))
    result = analysis.classify_ride_purpose(watts, ts, ftp)
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
    result = analysis.classify_ride_purpose(watts, ts, ftp)
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
    result = analysis.classify_ride_purpose(watts, ts, ftp)
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
    result = analysis.classify_ride_purpose(watts, ts, ftp)
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
    result = analysis.classify_ride_purpose(watts, ts, ftp)
    # Catch-all branch: pct=0.86>0.85 and dur_min<2 → "sprint" → "interval_sprints"
    assert result == "interval_sprints"


# ---------------------------------------------------------------------------
# _detect_intervals — edge cases
# ---------------------------------------------------------------------------


def test_detect_intervals_empty():
    assert analysis.detect_intervals([], [], 250) == []


def test_detect_intervals_zero_ftp():
    watts = [300.0] * 600
    ts = list(range(600))
    assert analysis.detect_intervals(watts, ts, 0) == []


def test_detect_intervals_merges_short_gaps():
    """Two intervals separated by a brief recovery should be merged."""
    ftp = 250
    work = round(ftp * 1.10)
    rest = round(ftp * 0.40)
    # 5 min work, 20 s rest (< recovery_gap_secs=30), 5 min work
    watts: list[float] = [work] * 300 + [rest] * 20 + [work] * 300
    ts = list(range(len(watts)))
    intervals = analysis.detect_intervals(watts, ts, ftp)
    # The two efforts should be merged into one
    assert len(intervals) == 1


def test_detect_intervals_ends_in_high_power():
    """Interval block that ends at the last sample should still be detected."""
    ftp = 250
    work = round(ftp * 1.10)
    rest = round(ftp * 0.40)
    watts: list[float] = [rest] * 300 + [work] * 300
    ts = list(range(len(watts)))
    intervals = analysis.detect_intervals(watts, ts, ftp)
    assert len(intervals) == 1
    assert intervals[0]["duration_secs"] >= 270


# ---------------------------------------------------------------------------
# _build_ride_analysis — edge cases
# ---------------------------------------------------------------------------


def test_build_ride_analysis_empty_streams():
    result = analysis.build_ride_analysis({}, 250.0)
    assert result == {}


def test_build_ride_analysis_no_time_data():
    streams = {"watts": {"data": [200.0] * 600}}
    result = analysis.build_ride_analysis(streams, 250.0)
    assert result == {}


def test_build_ride_analysis_no_hr_data():
    """Without HR data the function should still return a result."""
    ftp = 250.0
    watts = [ftp * 0.70] * 3600
    ts = list(range(3600))
    streams = {"watts": {"data": watts}, "time": {"data": ts}}
    result = analysis.build_ride_analysis(streams, ftp)
    assert "ride_category" in result
    assert result["ride_category"] == "endurance"
    assert result["duration_seconds"] == 3600


def test_build_ride_analysis_missing_watts_returns_unknown_with_duration():
    ts = list(range(5 * 60))
    streams = {"time": {"data": ts}}
    result = analysis.build_ride_analysis(streams, 250.0)
    assert result["ride_category"] == "unknown"
    assert result["duration_seconds"] == 300
    assert result["intervals_detected"] == []


def test_build_rule_based_summary_short_categories():
    summary = analysis.build_rule_based_summary(
        "short_easy_spin",
        15 * 60,
        normalized_power=170,
        tss=8,
        intervals=[],
    )
    assert summary.startswith("Short easy spin")


def test_build_ride_metrics_chain_missing_watts_marks_unknown():
    result = analysis.build_ride_metrics_chain(
        [
            {
                "strava_activity_id": 123,
                "activity_date": "2026-05-01",
                "sport_type": "cycling",
                "duration_seconds": 5 * 60,
                "streams": {"time": {"data": list(range(5 * 60))}},
            }
        ],
        ftp=250,
    )
    assert result[0]["ride_purpose"] == "unknown"
    assert result[0]["summary"].startswith("Unknown ride")


# ---------------------------------------------------------------------------
# _compute_hr_drift — short segment
# ---------------------------------------------------------------------------


def test_compute_hr_drift_too_short():
    assert analysis.compute_hr_drift([150.0, 155.0]) is None


def test_compute_hr_drift_flat():
    # Exactly flat HR — slope should be ~0
    hr = [150.0] * 100
    drift = analysis.compute_hr_drift(hr)
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
            "riderType": "allrounder",
            "notes": "Looks good.",
            "rideInsights": "Solid endurance ride.",
            "lastRideFeedback": "Nice effort today.",
        }
    )

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        return ai_response

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        result = await ai_service.analyse_strava_activities(
            activities, provider="openai", streams_by_id=streams_by_id
        )

    # FTP is never estimated from activity data — always null
    assert result["estimatedFTP"] is None
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
            "riderType": "endurance",
            "notes": "Good aerobic base.",
            "rideInsights": "Endurance ride.",
            "lastRideFeedback": "Solid session.",
        }
    )

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
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
            "riderType": "allrounder",
            "notes": "Good effort.",
            "rideInsights": "Solid ride.",
            "lastRideFeedback": "Nice work.",
        }
    )

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        return ai_response

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        result = await ai_service.analyse_strava_activities(activities, provider="openai")

    assert result["estimatedFTP"] is None  # FTP is always null — not estimated from rides


@pytest.mark.asyncio
async def test_generate_training_plan_calls_chat():
    """generate_training_plan should return the plan list from AI response."""
    profile = {
        "bikeType": "road",
        "trainingGoal": "general_fitness",
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

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
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

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        return json.dumps({"updatedDays": [updated_day]})

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        result = await ai_service.adapt_training_plan(
            plan, recent_feedback, profile, provider="openai"
        )

    assert result[0]["workoutType"] == "recovery"


@pytest.mark.asyncio
async def test_update_coach_memory_calls_chat():
    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
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

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        return json.dumps({"feedback": "Great effort today! You nailed the power targets.", "flag_for_adaptation": False})

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        result = await ai_service.rate_completed_workout(day, profile, provider="openai")

    assert isinstance(result, dict)
    assert "effort" in result["feedback"].lower() or "power" in result["feedback"].lower()
    assert result["flag_for_adaptation"] is False


@pytest.mark.asyncio
async def test_analyse_strava_activities_with_streams_and_hr():
    """Power-duration FTP should be computed when HR streams are present."""
    ftp = 280
    work_power = round(ftp / 0.95)
    watts = [float(work_power)] * 1500
    time_stream = list(range(1500))
    # HR data: hard enough to treat the 20-min power as FTP evidence.
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
            "riderType": "allrounder",
            "notes": "Good.",
            "rideInsights": "Solid.",
            "lastRideFeedback": "Nice.",
        }
    )

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        return ai_response

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        result = await ai_service.analyse_strava_activities(
            activities,
            provider="openai",
            streams_by_id=streams_by_id,
            max_heart_rate=max_hr,
        )

    # FTP is never estimated from activity data — always null
    assert result["estimatedFTP"] is None
    # HR zones should be populated
    assert result.get("hrZones") is not None


# ---------------------------------------------------------------------------
# compare_planned_vs_actual (analysis.py)
# ---------------------------------------------------------------------------


def test_compare_planned_vs_actual_empty_streams():
    """Returns empty dict when streams have no data."""
    result = analysis.compare_planned_vs_actual(
        planned={"workoutType": "endurance", "durationMinutes": 60},
        streams={},
    )
    assert result == {}


def test_compare_planned_vs_actual_avg_power():
    """avg_power_w is the mean of the watts stream."""
    watts = [200.0, 210.0, 220.0]
    time_stream = [0.0, 1.0, 2.0]
    result = analysis.compare_planned_vs_actual(
        planned={},
        streams={"watts": {"data": watts}, "time": {"data": time_stream}},
    )
    assert result["avg_power_w"] == round(sum(watts) / len(watts))


def test_compare_planned_vs_actual_power_delta():
    """avg_power_delta_pct reflects deviation from target midpoint."""
    # Target midpoint = (190 + 220) / 2 = 205 W
    # Actual avg = 230 W → delta = (230 - 205) / 205 * 100 ≈ +12.2 %
    watts = [230.0] * 10
    time_stream = list(range(10))
    result = analysis.compare_planned_vs_actual(
        planned={"targetPower": {"low": 190, "high": 220}},
        streams={"watts": {"data": watts}, "time": {"data": time_stream}},
    )
    assert result["target_power_low"] == 190
    assert result["target_power_high"] == 220
    assert result["avg_power_delta_pct"] > 0  # over target


def test_compare_planned_vs_actual_time_in_zones():
    """time_in_zones sums to approximately total ride duration."""
    ftp = 200.0
    # 10 seconds each at Z1 (100W), Z2 (130W), Z3 (170W) power
    watts = [100.0] * 10 + [130.0] * 10 + [170.0] * 10
    time_stream = list(range(30))
    result = analysis.compare_planned_vs_actual(
        planned={},
        streams={"watts": {"data": watts}, "time": {"data": time_stream}},
        ftp=ftp,
    )
    tiz = result["time_in_zones"]
    # 100W = 50% FTP → Z1; 130W = 65% FTP → Z2; 170W = 85% FTP → Z3
    assert tiz["z1_secs"] > 0
    assert tiz["z2_secs"] > 0
    assert tiz["z3_secs"] > 0
    # Total time in zones should be close to 29 seconds (30 samples, last sample uses dt=1)
    total = sum(tiz.values())
    assert total >= 28


def test_compare_planned_vs_actual_hr_drift():
    """hr_drift_bpm is positive when HR rises steadily."""
    watts = [200.0] * 20
    time_stream = list(range(20))
    # HR rises from 140 to 159 bpm (steady drift)
    hr = [140.0 + i for i in range(20)]
    result = analysis.compare_planned_vs_actual(
        planned={},
        streams={
            "watts": {"data": watts},
            "time": {"data": time_stream},
            "heartrate": {"data": hr},
        },
    )
    assert result["avg_hr_bpm"] is not None
    # Positive drift expected
    assert result.get("hr_drift_bpm", 0) > 0


def test_compare_planned_vs_actual_intensity_spikes():
    """intensity_spikes is populated when power significantly exceeds target."""
    target_mid = 200.0  # target 180–220 W
    # 1000 seconds at 250W (25 % over target midpoint)
    watts = [250.0] * 1000
    time_stream = list(range(1000))
    result = analysis.compare_planned_vs_actual(
        planned={"targetPower": {"low": 180, "high": 220}},
        streams={"watts": {"data": watts}, "time": {"data": time_stream}},
    )
    spikes = result.get("intensity_spikes", [])
    assert len(spikes) > 0
    assert spikes[0]["pct_over_target"] > 10.0


def test_compare_planned_vs_actual_no_spikes_within_target():
    """intensity_spikes is empty when power is within the target range."""
    watts = [205.0] * 1000  # within 180–220 W target
    time_stream = list(range(1000))
    result = analysis.compare_planned_vs_actual(
        planned={"targetPower": {"low": 180, "high": 220}},
        streams={"watts": {"data": watts}, "time": {"data": time_stream}},
    )
    assert result.get("intensity_spikes", []) == []


# ---------------------------------------------------------------------------
# rate_completed_workout with stream_delta
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_completed_workout_with_stream_delta():
    """rate_completed_workout passes stream_delta info into the user prompt."""
    day = {
        "workoutType": "endurance",
        "title": "Z2 Ride",
        "description": "Easy endurance ride",
        "durationMinutes": 90,
        "targetPower": {"low": 180, "high": 210},
        "feedback": {
            "actualDurationMinutes": 88,
            "perceivedEffort": 3,
            "averagePower": 225,
            "averageHeartRate": 155,
            "notes": "Felt strong",
            "completedAt": "2026-04-10T10:00:00Z",
        },
    }
    profile = {"fitnessLevel": "intermediate"}
    stream_delta = {
        "avg_power_w": 225,
        "normalized_power_w": 232,
        "target_power_low": 180,
        "target_power_high": 210,
        "avg_power_delta_pct": 16.1,
        "normalized_power_delta_pct": 20.3,
        "hr_drift_bpm": 12.0,
        "intensity_spikes": [
            {"start_min": 0.0, "end_min": 15.0, "avg_power_w": 240, "pct_over_target": 24.4}
        ],
    }

    captured_user_msg: list[str] = []

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        captured_user_msg.append(user_msg)
        return json.dumps({"feedback": "You went over intensity — ease back next time.", "flag_for_adaptation": False})

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        result = await ai_service.rate_completed_workout(
            day, profile, provider="openai", stream_delta=stream_delta
        )

    assert result["feedback"] != ""
    assert result["flag_for_adaptation"] is False
    # The stream delta data should appear in the prompt
    assert captured_user_msg, "fake_chat was not called"
    msg = captured_user_msg[0]
    assert "stream data" in msg.lower() or "strava" in msg.lower() or "16.1" in msg



# ---------------------------------------------------------------------------
# _compute_training_load (Task 1)
# ---------------------------------------------------------------------------


def test_compute_training_load_empty():
    result = analysis.compute_training_load([], 250.0)
    assert result == {"ctl": 0.0, "atl": 0.0, "tsb": 0.0, "daily_tss": []}


def test_compute_training_load_zero_ftp():
    plan = [{"durationMinutes": 60, "workoutType": "endurance"}]
    result = analysis.compute_training_load(plan, 0.0)
    assert result == {"ctl": 0.0, "atl": 0.0, "tsb": 0.0, "daily_tss": []}


def test_compute_training_load_returns_expected_keys():
    plan = [
        {"durationMinutes": 90, "workoutType": "endurance"},
        {"durationMinutes": 0, "workoutType": "rest"},
        {"durationMinutes": 60, "workoutType": "intervals", "targetPower": {"low": 280, "high": 320}},
    ]
    result = analysis.compute_training_load(plan, 300.0)
    assert "ctl" in result and "atl" in result and "tsb" in result and "daily_tss" in result
    assert len(result["daily_tss"]) == 3
    # Rest day should produce 0 TSS
    assert result["daily_tss"][1] == 0.0
    # TSB = CTL - ATL (may differ by up to 0.2 due to independent rounding)
    assert abs(result["tsb"] - (result["ctl"] - result["atl"])) < 0.2


def test_compute_training_load_target_power():
    """When targetPower is given, TSS should use mid-point for IF calculation."""
    plan = [{"durationMinutes": 60, "workoutType": "endurance", "targetPower": {"low": 240, "high": 260}}]
    result = analysis.compute_training_load(plan, 250.0)
    # mid-point = 250W, FTP = 250W → IF = 1.0, TSS = (3600 × 250 × 1) / (250 × 3600) × 100 = 100
    assert abs(result["daily_tss"][0] - 100.0) < 1.0


def test_compute_training_load_tsb_equals_ctl_minus_atl():
    plan = [{"durationMinutes": 60, "workoutType": "tempo"}] * 10
    result = analysis.compute_training_load(plan, 250.0)
    # TSB = CTL - ATL (may differ by up to 0.2 due to independent rounding of each value)
    assert abs(result["tsb"] - (result["ctl"] - result["atl"])) < 0.2


# ---------------------------------------------------------------------------
# compute_readiness_score
# ---------------------------------------------------------------------------


def test_readiness_score_returns_expected_keys():
    result = analysis.compute_readiness_score(ctl=50.0, atl=60.0, tsb=-10.0, days_until_race=7)
    for key in ("score", "form_score", "fitness_score", "ctl", "atl", "tsb", "days_until_race"):
        assert key in result


def test_readiness_score_range():
    """Score and component scores must be in [0, 100]."""
    for tsb in (-40, -20, 0, 10, 25, 50):
        result = analysis.compute_readiness_score(ctl=60.0, atl=70.0, tsb=float(tsb), days_until_race=0)
        assert 0.0 <= result["score"] <= 100.0
        assert 0.0 <= result["form_score"] <= 100.0
        assert 0.0 <= result["fitness_score"] <= 100.0


def test_readiness_score_peak_form():
    """TSB near +10 with high CTL should produce a high score."""
    result = analysis.compute_readiness_score(ctl=80.0, atl=70.0, tsb=10.0, days_until_race=0)
    assert result["form_score"] == 100.0
    assert result["score"] > 80.0


def test_readiness_score_severe_fatigue():
    """TSB ≤ -30 should give form_score 0."""
    result = analysis.compute_readiness_score(ctl=50.0, atl=80.0, tsb=-30.0, days_until_race=14)
    assert result["form_score"] == 0.0
    assert result["score"] < 40.0


def test_readiness_score_zero_ctl():
    """Zero CTL and zero ATL (TSB=0) → form_score=50, fitness_score=0, score=32.5."""
    result = analysis.compute_readiness_score(ctl=0.0, atl=0.0, tsb=0.0, days_until_race=0)
    assert result["form_score"] == 50.0
    assert result["fitness_score"] == 0.0
    assert result["score"] == 32.5


def test_readiness_score_days_until_race_preserved():
    result = analysis.compute_readiness_score(ctl=60.0, atl=55.0, tsb=5.0, days_until_race=21)
    assert result["days_until_race"] == 21


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
    "trainingGoal": "general_fitness",
    "fitnessLevel": "intermediate",
    "currentFTP": 300,
}


@pytest.mark.asyncio
async def test_ask_trainer_thinking_not_in_return():
    """'thinking' must be stripped from the return value and never reach the frontend."""

    async def fake_chat_history(provider, system_prompt, messages, json_mode=False, **kwargs):
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

    async def fake_chat_history(provider, system_prompt, messages, json_mode=False, **kwargs):
        captured_system_prompt.append(system_prompt)
        return json.dumps({"response": "Here's your plan.", "planUpdates": [], "sources": []})

    with patch.object(ai_service, "_chat_history", side_effect=fake_chat_history):
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

    async def fake_chat_history(provider, system_prompt, messages, json_mode=False, **kwargs):
        captured_prompt.append(system_prompt)
        return json.dumps({"response": "OK.", "planUpdates": [], "sources": []})

    with patch.object(ai_service, "_chat_history", side_effect=fake_chat_history):
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

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
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

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
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
# onboarding race context prompts
# ---------------------------------------------------------------------------


def test_race_profile_context_empty_without_race_date_or_description():
    from services.prompts import race_profile_context_section

    assert race_profile_context_section({"trainingGoal": "general_fitness"}) == ""


def test_race_profile_context_includes_race_date_and_description():
    from services.prompts import race_profile_context_section

    section = race_profile_context_section(
        {
            "trainingGoal": "general_fitness",
            "raceDate": "2026-09-15",
            "raceDescription": "Local gran fondo with repeated climbs",
        }
    )

    assert "Profile race context" in section
    assert "Race date: 2026-09-15" in section
    assert "race-specific preparation and taper timing" in section
    assert "Local gran fondo with repeated climbs" in section
    assert "event context for training decisions" in section


def test_generate_adapt_and_chat_prompts_include_profile_race_context():
    from services.prompts import (
        adapt_plan_user,
        ask_trainer_plan_updates_rule,
        ask_trainer_system,
        generate_plan_user,
    )

    profile = {
        "trainingGoal": "general_fitness",
        "raceDate": "2026-09-15",
        "raceDescription": "Flat time trial with crosswinds",
        "fitnessLevel": "intermediate",
    }
    generate_prompt = generate_plan_user(
        profile,
        today="2026-05-10",
        assessment_section="",
    )
    adapt_prompt = adapt_plan_user(
        profile,
        today="2026-05-10",
        recent_feedback=[],
        incomplete_days=[],
    )
    chat_prompt = ask_trainer_system(
        profile=profile,
        today="2026-05-10",
        last_7_days=[],
        next_n_days=[],
        assessment_section="",
        memory_section="",
        workout_section="",
        plan_updates_rule=ask_trainer_plan_updates_rule(None),
    )

    for prompt in (generate_prompt, adapt_prompt, chat_prompt):
        assert "Profile race context" in prompt
        assert "Flat time trial with crosswinds" in prompt
        assert "Onboarding goal priority" not in prompt


def test_generate_plan_user_omits_race_context_for_general_fitness():
    from services.prompts import generate_plan_user

    prompt = generate_plan_user(
        {
            "trainingGoal": "general_fitness",
            "fitnessLevel": "intermediate",
        },
        today="2026-05-10",
        assessment_section="",
    )

    assert "Profile race context" not in prompt
    assert "Onboarding goal priority" not in prompt


def test_race_events_context_keeps_fixed_distance_elevation_commitments():
    from services.prompts import race_events_context_section

    section = race_events_context_section(
        [
            {
                "date": "2026-09-15",
                "startTime": "08:30",
                "distanceKm": 145.0,
                "elevationM": 2400,
            }
        ]
    )

    assert "Race calendar (fixed athlete events to plan around)" in section
    assert "2026-09-15 at 08:30: 145 km with 2400 m climbing" in section
    assert "Treat these events as real calendar commitments" in section
    assert "terrain-specific work" in section


# ---------------------------------------------------------------------------
# classify_question (Task 5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_question_returns_default_on_failure():
    """classify_question must return a safe default if the AI call fails."""
    async def broken_chat(provider, system_prompt, user_msg, json_mode=False):
        raise RuntimeError("API unavailable")

    with patch.object(ai_service, "_chat", side_effect=broken_chat):
        result = await ai_service.classify_question("What's my FTP?")

    assert result["category"] == "general_coaching"
    assert result["needs_science_rag"] is False


@pytest.mark.asyncio
async def test_classify_question_parses_response():
    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        return json.dumps({"category": "science_question", "needs_science_rag": True})

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        result = await ai_service.classify_question("How does VO2max training work?")

    assert result["category"] == "science_question"
    assert result["needs_science_rag"] is True


# ---------------------------------------------------------------------------
# generate_training_plan — workoutPurpose and keyFocusPoints fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_plan_workout_purpose_and_focus_points_present():
    """Every non-rest day returned by generate_training_plan must have workoutPurpose
    (non-empty string) and keyFocusPoints (list with at least 3 entries)."""
    profile = {
        "bikeType": "road",
        "trainingGoal": "general_fitness",
        "fitnessLevel": "intermediate",
        "currentFTP": 260,
    }
    fake_plan = [
        {
            "date": "2026-04-14",
            "workoutType": "endurance",
            "title": "Zone 2 Endurance",
            "description": "Ride 90 min at 195–220 W (Zone 2, 75–85% of your 260 W FTP). Keep HR under 148 bpm.",
            "durationMinutes": 90,
            "targetPower": {"low": 195, "high": 220},
            "workoutPurpose": "Builds aerobic base and improves fat oxidation. Placed early in the week to accumulate low-intensity volume before harder sessions.",
            "keyFocusPoints": [
                "Keep cadence between 88–95 rpm throughout",
                "HR must stay below 148 bpm; back off on any climbs if it creeps higher",
                "Breathe comfortably — you should be able to hold a conversation",
            ],
        },
        {
            "date": "2026-04-15",
            "workoutType": "rest",
            "title": "Rest Day",
            "description": "Complete rest.",
            "durationMinutes": 0,
        },
        {
            "date": "2026-04-16",
            "workoutType": "intervals",
            "title": "VO2max Intervals",
            "description": "5×4 min at 312–338 W (120–130% of your 260 W FTP) with 4 min easy recovery between reps.",
            "durationMinutes": 70,
            "targetPower": {"low": 312, "high": 338},
            "workoutPurpose": "Raises VO2max by stressing the cardiovascular system at near-maximal intensity. Positioned mid-week after a rest day for maximum freshness.",
            "keyFocusPoints": [
                "Start each interval at a rolling pace, not a standing sprint",
                "Target 90–95 rpm cadence during the hard efforts",
                "If HR climbs above 185 bpm before the interval ends, back off slightly",
                "Recovery spins should stay below 130 W",
            ],
        },
    ]

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        return json.dumps({"plan": fake_plan})

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        result = await ai_service.generate_training_plan(profile, provider="openai")

    non_rest_days = [day for day in result if day.get("workoutType") != "rest"]
    assert len(non_rest_days) > 0, "Expected at least one non-rest day in the plan"
    for day in non_rest_days:
        assert day.get("workoutPurpose"), (
            f"Day {day['date']} ({day['workoutType']}) is missing workoutPurpose"
        )
        focus_points = day.get("keyFocusPoints", [])
        assert isinstance(focus_points, list) and len(focus_points) >= 3, (
            f"Day {day['date']} ({day['workoutType']}) must have at least 3 keyFocusPoints, "
            f"got {len(focus_points)}"
        )


# ---------------------------------------------------------------------------
# COACH_PERSONA / RUNNING_COACH_PERSONA — balanced coach voice
# ---------------------------------------------------------------------------


def test_coach_persona_is_warm_but_not_overfamiliar():
    """COACH_PERSONA must stay warm without buddy-style overfamiliarity."""
    from services.prompts import COACH_PERSONA

    text = COACH_PERSONA.lower()
    assert "warm" in text and "personal" in text and "respectful" in text and "direct" in text
    assert "cycling" in COACH_PERSONA.lower(), "COACH_PERSONA must mention cycling"
    assert "great friend" not in text
    assert "my friend" not in text
    assert "supportive buddy" not in text


def test_running_coach_persona_is_warm_but_not_overfamiliar():
    """RUNNING_COACH_PERSONA must use the same balanced voice as COACH_PERSONA."""
    from services.prompts import RUNNING_COACH_PERSONA

    text = RUNNING_COACH_PERSONA.lower()
    assert "warm" in text and "personal" in text and "respectful" in text and "direct" in text
    assert "running" in text
    assert "great friend" not in text
    assert "my friend" not in text
    assert "supportive buddy" not in text


def test_coach_persona_handles_jokes_and_sarcasm():
    """Both personas must understand jokes and sarcasm without losing the coaching thread."""
    from services.prompts import COACH_PERSONA, RUNNING_COACH_PERSONA

    for persona in (COACH_PERSONA, RUNNING_COACH_PERSONA):
        text = persona.lower()
        assert "take the athlete seriously" in text
        assert "joking" in text
        assert "sarcastic" in text
        assert "useful coaching" in text


def test_coach_persona_encourages_concise_answers():
    """Both personas must keep the default answer length focused."""
    from services.prompts import COACH_PERSONA, RUNNING_COACH_PERSONA

    for persona in (COACH_PERSONA, RUNNING_COACH_PERSONA):
        text = persona.lower()
        assert "concise" in text
        assert "2-4 focused sentences" in text


def test_coach_persona_keeps_replies_personal():
    """Both personas must make replies feel specific to the athlete."""
    from services.prompts import COACH_PERSONA, RUNNING_COACH_PERSONA

    for persona in (COACH_PERSONA, RUNNING_COACH_PERSONA):
        text = persona.lower()
        assert "first name" in text
        assert "specific to their goals" in text
        assert "recent training" in text
        assert "personal detail" in text
        assert "coach memory" in text
        assert "feel seen" in text


def test_coach_persona_has_balanced_conversational_tone():
    """Both personas must steer toward natural warmth without performative friendliness."""
    from services.prompts import COACH_PERSONA, RUNNING_COACH_PERSONA

    for persona in (COACH_PERSONA, RUNNING_COACH_PERSONA):
        text = persona.lower()
        assert "balanced conversational tone" in text
        assert "attentive" in text
        assert "natural" in text
        assert "calmly confident" in text
        assert "neither robotic nor performatively friendly" in text


def test_personas_are_sport_distinct():
    """The two personas must be distinct and each reference their own sport."""
    from services.prompts import COACH_PERSONA, RUNNING_COACH_PERSONA

    assert COACH_PERSONA != RUNNING_COACH_PERSONA
    assert "cycling" in COACH_PERSONA and "cycling" not in RUNNING_COACH_PERSONA
    assert "running" in RUNNING_COACH_PERSONA and "running" not in COACH_PERSONA


def test_coach_persona_empathy_traits():
    """COACH_PERSONA must instruct the AI to show empathy and avoid judgement."""
    from services.prompts import COACH_PERSONA

    text = COACH_PERSONA.lower()
    assert "empathy" in text or "empathetic" in text or "compassion" in text
    assert "judged" in text or "judgment" in text or "judge" in text


def test_coach_persona_encourages_direct_address():
    """Both personas must instruct the AI to address the athlete directly with 'you'."""
    from services.prompts import COACH_PERSONA, RUNNING_COACH_PERSONA

    for persona in (COACH_PERSONA, RUNNING_COACH_PERSONA):
        assert "address the athlete directly" in persona.lower() or "'you'" in persona


def test_coach_persona_retains_long_term_philosophy():
    """Both personas must still communicate the long-term development philosophy."""
    from services.prompts import COACH_PERSONA, RUNNING_COACH_PERSONA

    for persona in (COACH_PERSONA, RUNNING_COACH_PERSONA):
        assert "long-term" in persona.lower()
        assert "recovery" in persona.lower()


def test_analyse_activities_system_uses_correct_persona():
    """analyse_activities_system must embed the sport-appropriate persona."""
    from services.prompts import analyse_activities_system, COACH_PERSONA, RUNNING_COACH_PERSONA

    cycling_system = analyse_activities_system("cycling")
    running_system = analyse_activities_system("running")

    # Each system prompt must start with the matching persona
    assert cycling_system.startswith(COACH_PERSONA)
    assert running_system.startswith(RUNNING_COACH_PERSONA)
    # And must not bleed into the wrong sport
    assert "You are a knowledgeable cycling coach" not in running_system
    assert "You are a knowledgeable running coach" not in cycling_system


def test_generate_plan_system_uses_coach_persona():
    """generate_plan_system must embed COACH_PERSONA (cycling plans only)."""
    from services.prompts import generate_plan_system, COACH_PERSONA

    system = generate_plan_system()
    assert system.startswith(COACH_PERSONA)


def test_rate_workout_system_uses_coach_persona():
    """rate_workout_system must embed COACH_PERSONA."""
    from services.prompts import rate_workout_system, COACH_PERSONA

    system = rate_workout_system()
    assert system.startswith(COACH_PERSONA)


def test_coach_voice_traits_template_interpolation():
    """_COACH_VOICE_TRAITS must resolve without placeholders for known sport names."""
    from services.prompts import _COACH_VOICE_TRAITS

    for sport in ("cycling", "running"):
        resolved = _COACH_VOICE_TRAITS.format(sport=sport)
        assert sport in resolved
        assert "{sport}" not in resolved


# ---------------------------------------------------------------------------
# classify_ride_confidence_and_reason
# ---------------------------------------------------------------------------


def test_classify_ride_confidence_unknown_is_low():
    confidence, reason = analysis.classify_ride_confidence_and_reason("unknown", 600, [])
    assert confidence == "low"
    assert len(reason) > 0


def test_classify_ride_confidence_short_easy_spin_is_low():
    confidence, reason = analysis.classify_ride_confidence_and_reason("short_easy_spin", 15 * 60, [])
    assert confidence == "low"


def test_classify_ride_confidence_short_hard_effort_is_low():
    confidence, reason = analysis.classify_ride_confidence_and_reason("short_hard_effort", 20 * 60, [])
    assert confidence == "low"


def test_classify_ride_confidence_recovery_long_is_high():
    # MIN_ENDURANCE_RIDE_SECS = 30 min
    confidence, reason = analysis.classify_ride_confidence_and_reason("recovery", 45 * 60, [])
    assert confidence == "high"


def test_classify_ride_confidence_recovery_short_is_medium():
    confidence, reason = analysis.classify_ride_confidence_and_reason("recovery", 10 * 60, [])
    assert confidence == "medium"


def test_classify_ride_confidence_endurance_long_is_high():
    confidence, reason = analysis.classify_ride_confidence_and_reason("endurance", 60 * 60, [])
    assert confidence == "high"


def test_classify_ride_confidence_endurance_short_is_medium():
    confidence, reason = analysis.classify_ride_confidence_and_reason("endurance", 30 * 60, [])
    assert confidence == "medium"


def test_classify_ride_confidence_tempo_is_medium():
    confidence, reason = analysis.classify_ride_confidence_and_reason("tempo", 60 * 60, [])
    assert confidence == "medium"


def test_classify_ride_confidence_threshold_two_intervals_is_high():
    fake_intervals = [{"duration_secs": 480}, {"duration_secs": 480}]
    confidence, reason = analysis.classify_ride_confidence_and_reason(
        "interval_threshold", 60 * 60, fake_intervals
    )
    assert confidence == "high"


def test_classify_ride_confidence_sweetspot_one_interval_is_medium():
    fake_intervals = [{"duration_secs": 900}]
    confidence, reason = analysis.classify_ride_confidence_and_reason(
        "interval_sweetspot", 60 * 60, fake_intervals
    )
    assert confidence == "medium"


def test_classify_ride_confidence_mixed_is_medium():
    confidence, reason = analysis.classify_ride_confidence_and_reason("mixed", 90 * 60, [])
    assert confidence == "medium"


def test_classify_ride_confidence_returns_string_reason():
    """reason must always be a non-empty string for every category."""
    categories = [
        "unknown", "short_easy_spin", "short_hard_effort",
        "recovery", "endurance", "tempo",
        "interval_sweetspot", "interval_threshold", "interval_vo2max", "interval_sprints",
        "mixed",
    ]
    for cat in categories:
        _, reason = analysis.classify_ride_confidence_and_reason(cat, 60 * 60, [])
        assert isinstance(reason, str) and len(reason) > 0, f"Empty reason for category {cat!r}"


# ---------------------------------------------------------------------------
# build_ride_analysis — classification_confidence and classification_reason
# ---------------------------------------------------------------------------


def test_build_ride_analysis_includes_confidence_and_reason():
    ftp = 250.0
    watts = [ftp * 0.70] * 3600
    ts = list(range(3600))
    streams = {"watts": {"data": watts}, "time": {"data": ts}}
    result = analysis.build_ride_analysis(streams, ftp)
    assert "classification_confidence" in result
    assert "classification_reason" in result
    assert result["classification_confidence"] in ("high", "medium", "low")
    assert isinstance(result["classification_reason"], str)


def test_build_ride_analysis_missing_watts_includes_confidence():
    ts = list(range(5 * 60))
    streams = {"time": {"data": ts}}
    result = analysis.build_ride_analysis(streams, 250.0)
    assert result["ride_category"] == "unknown"
    assert result["classification_confidence"] == "low"
    assert isinstance(result["classification_reason"], str)


# ---------------------------------------------------------------------------
# build_ride_metrics_chain — classification fields propagated
# ---------------------------------------------------------------------------


def test_build_ride_metrics_chain_includes_classification_fields():
    ftp = 250
    watts = [ftp * 0.68] * (60 * 60)
    ts = list(range(len(watts)))
    rides = [
        {
            "strava_activity_id": 1,
            "activity_date": "2026-05-01",
            "sport_type": "cycling",
            "duration_seconds": 60 * 60,
            "streams": {"watts": {"data": watts}, "time": {"data": ts}},
        }
    ]
    result = analysis.build_ride_metrics_chain(rides, ftp=ftp)
    assert len(result) == 1
    row = result[0]
    assert "classification_confidence" in row
    assert "classification_reason" in row
    assert row["classification_confidence"] in ("high", "medium", "low")
    assert isinstance(row["classification_reason"], str) and len(row["classification_reason"]) > 0


def test_build_ride_metrics_chain_missing_streams_sets_low_confidence():
    rides = [
        {
            "strava_activity_id": 2,
            "activity_date": "2026-05-02",
            "sport_type": "cycling",
            "duration_seconds": 3600,
            "streams": {},
        }
    ]
    result = analysis.build_ride_metrics_chain(rides, ftp=250)
    assert result[0]["ride_purpose"] == "unknown"
    assert result[0]["classification_confidence"] == "low"


# ---------------------------------------------------------------------------
# ride_metrics_context_section — confidence/reason in prompt output
# ---------------------------------------------------------------------------


def test_ride_metrics_context_section_shows_confidence():
    from services.prompts import ride_metrics_context_section

    class FakeMetric:
        activity_date = "2026-05-01"
        ride_purpose = "endurance"
        classification_confidence = "high"
        classification_reason = "Sustained aerobic effort across adequate ride duration."
        tss = 80.0
        normalized_power_w = 190
        ctl_after = 55.0
        atl_after = 60.0
        tsb_after = -5.0
        summary = "60 min Z2 @ 190W NP"
        coach_note = None
        user_note = None

    section = ride_metrics_context_section([FakeMetric()])
    assert "conf:high" in section


def test_ride_metrics_context_section_low_confidence_shows_reason():
    from services.prompts import ride_metrics_context_section

    class FakeMetric:
        activity_date = "2026-05-01"
        ride_purpose = "short_easy_spin"
        classification_confidence = "low"
        classification_reason = "Ride too short for a reliable aerobic classification."
        tss = 8.0
        normalized_power_w = None
        ctl_after = None
        atl_after = None
        tsb_after = None
        summary = None
        coach_note = None
        user_note = None

    section = ride_metrics_context_section([FakeMetric()])
    assert "conf:low" in section
    assert "Ride too short" in section


def test_ride_metrics_context_section_high_confidence_omits_reason_line():
    """For high-confidence rides the reason sub-line should not appear."""
    from services.prompts import ride_metrics_context_section

    class FakeMetric:
        activity_date = "2026-04-01"
        ride_purpose = "endurance"
        classification_confidence = "high"
        classification_reason = "Sustained aerobic effort across adequate ride duration."
        tss = 90.0
        normalized_power_w = 200
        ctl_after = 60.0
        atl_after = 65.0
        tsb_after = -5.0
        summary = None
        coach_note = None
        user_note = None

    section = ride_metrics_context_section([FakeMetric()])
    # The reason detail line should NOT appear for high-confidence rides
    assert "[classification:" not in section


def test_ride_metrics_context_section_medium_confidence_shows_reason_line():
    from services.prompts import ride_metrics_context_section

    class FakeMetric:
        activity_date = "2026-04-01"
        ride_purpose = "tempo"
        classification_confidence = "medium"
        classification_reason = "Average power in tempo band with no distinct interval blocks detected."
        tss = 60.0
        normalized_power_w = 220
        ctl_after = 55.0
        atl_after = 58.0
        tsb_after = -3.0
        summary = None
        coach_note = None
        user_note = None

    section = ride_metrics_context_section([FakeMetric()])
    assert "[classification:" in section
    assert "tempo band" in section


def test_ride_metrics_context_section_no_confidence_field_is_graceful():
    """If classification_confidence is absent the section should still render."""
    from services.prompts import ride_metrics_context_section

    class FakeMetric:
        activity_date = "2026-04-01"
        ride_purpose = "endurance"
        # no classification_confidence / classification_reason attributes
        tss = 70.0
        normalized_power_w = 185
        ctl_after = 50.0
        atl_after = 55.0
        tsb_after = -5.0
        summary = None
        coach_note = None
        user_note = None

    section = ride_metrics_context_section([FakeMetric()])
    assert "endurance" in section
    # conf: should not appear when the attribute is missing
    assert "conf:" not in section


# ---------------------------------------------------------------------------
# batch_review_user prompt — content checks
# ---------------------------------------------------------------------------


class _FakeRide:
    """Minimal duck-typed RideMetric for prompt tests."""

    def __init__(
        self,
        activity_date: str,
        ride_purpose: str = "endurance",
        classification_confidence: str = "high",
        classification_reason: str | None = None,
        duration_seconds: int = 3600,
        tss: float = 80.0,
        normalized_power_w: int | None = 200,
        avg_power_w: int | None = None,
        ctl_after: float | None = 55.0,
        atl_after: float | None = 60.0,
        tsb_after: float | None = -5.0,
        coach_note: str | None = None,
        user_note: str | None = None,
    ) -> None:
        self.activity_date = activity_date
        self.ride_purpose = ride_purpose
        self.classification_confidence = classification_confidence
        self.classification_reason = classification_reason
        self.duration_seconds = duration_seconds
        self.tss = tss
        self.normalized_power_w = normalized_power_w
        self.avg_power_w = avg_power_w
        self.ctl_after = ctl_after
        self.atl_after = atl_after
        self.tsb_after = tsb_after
        self.coach_note = coach_note
        self.user_note = user_note


def test_batch_review_user_contains_all_ride_dates():
    from services.prompts import batch_review_user

    rides = [
        _FakeRide("2026-05-01"),
        _FakeRide("2026-05-02"),
        _FakeRide("2026-05-03"),
    ]
    msg = batch_review_user(rides, profile={"name": "Alice"})
    assert "2026-05-01" in msg
    assert "2026-05-02" in msg
    assert "2026-05-03" in msg


def test_batch_review_user_includes_tss_and_np():
    from services.prompts import batch_review_user

    ride = _FakeRide("2026-05-01", tss=95.0, normalized_power_w=240)
    msg = batch_review_user([ride])
    assert "TSS: 95" in msg
    assert "NP: 240W" in msg


def test_batch_review_user_includes_classification_reason_for_low_confidence():
    from services.prompts import batch_review_user

    ride = _FakeRide(
        "2026-05-01",
        ride_purpose="short_easy_spin",
        classification_confidence="low",
        classification_reason="Ride too short for a reliable classification.",
    )
    msg = batch_review_user([ride])
    assert "Ride too short" in msg


def test_batch_review_user_omits_classification_reason_for_high_confidence():
    from services.prompts import batch_review_user

    ride = _FakeRide(
        "2026-05-01",
        ride_purpose="endurance",
        classification_confidence="high",
        classification_reason="Sustained aerobic effort.",
    )
    msg = batch_review_user([ride])
    # Reason detail should not leak for high-confidence rides
    assert "Sustained aerobic effort" not in msg


def test_batch_review_user_includes_training_plan():
    from services.prompts import batch_review_user

    ride = _FakeRide("2026-05-01")
    plan = [{"date": "2026-05-01", "workoutType": "endurance", "durationMinutes": 90}]
    msg = batch_review_user([ride], training_plan=plan)
    assert "Training plan" in msg
    assert "endurance" in msg


def test_batch_review_user_includes_coach_and_athlete_notes():
    from services.prompts import batch_review_user

    ride = _FakeRide(
        "2026-05-01",
        coach_note="Good effort.",
        user_note="Felt heavy.",
    )
    msg = batch_review_user([ride])
    assert "Good effort." in msg
    assert "Felt heavy." in msg


def test_ride_metrics_context_section_includes_matched_plan_snapshot():
    from services.prompts import ride_metrics_context_section

    class FakeMetric:
        activity_date = "2026-05-06"
        ride_purpose = "tempo"
        sport_type = "cycling"
        classification_confidence = "high"
        classification_reason = None
        tss = 75
        normalized_power_w = 235
        ctl_after = 40.0
        atl_after = 50.0
        tsb_after = -10.0
        summary = "Tempo ride"
        coach_note = "Good match."
        user_note = "Felt controlled."
        plan_match_status = "manual_matched"
        matched_plan_date = "2026-05-06"
        matched_plan_snapshot = {"title": "Planned Tempo", "durationMinutes": 80}

    section = ride_metrics_context_section([FakeMetric()])
    assert "plan match:manual_matched" in section
    assert "Planned workout: Planned Tempo, 80 min" in section


def test_batch_review_user_includes_ambiguous_plan_match():
    from services.prompts import batch_review_user

    ride = _FakeRide(
        "2026-05-06",
        ride_purpose="unknown",
        classification_confidence="low",
        classification_reason="Multiple same-day rides need athlete selection.",
    )
    ride.plan_match_status = "ambiguous"
    ride.matched_plan_snapshot = {"title": "Planned Intervals", "durationMinutes": 75}

    msg = batch_review_user(
        [ride],
        training_plan=[{"date": "2026-05-06", "title": "Planned Intervals"}],
    )
    assert "Plan match: ambiguous" in msg
    assert "Matched planned workout: Planned Intervals" in msg


def test_batch_review_user_empty_rides():
    from services.prompts import batch_review_user

    msg = batch_review_user([])
    assert "Rides to review" in msg


# ---------------------------------------------------------------------------
# batch_review_rides service function
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_review_rides_returns_empty_for_no_rides():
    result = await ai_service.batch_review_rides([], profile={"name": "Alice"})
    assert result == ""


@pytest.mark.asyncio
async def test_batch_review_rides_calls_chat_with_all_rides():
    captured_user_msgs: list[str] = []

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        captured_user_msgs.append(user_msg)
        return json.dumps({"review": "Good block of training."})

    rides = [
        _FakeRide("2026-05-01"),
        _FakeRide("2026-05-02"),
        _FakeRide("2026-05-03"),
    ]
    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        result = await ai_service.batch_review_rides(
            rides, profile={"name": "Alice"}, provider="openai"
        )

    assert result == "Good block of training."
    assert len(captured_user_msgs) == 1
    msg = captured_user_msgs[0]
    # All three ride dates must appear in the prompt
    assert "2026-05-01" in msg
    assert "2026-05-02" in msg
    assert "2026-05-03" in msg


@pytest.mark.asyncio
async def test_batch_review_rides_single_ride_still_works():
    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        return json.dumps({"review": "Nice endurance ride."})

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        result = await ai_service.batch_review_rides(
            [_FakeRide("2026-05-01")], profile={}
        )

    assert result == "Nice endurance ride."


# ---------------------------------------------------------------------------
# Task 4: rate_completed_workout — follow-up dialogue fields
# ---------------------------------------------------------------------------


def test_rate_workout_system_prompt_includes_follow_up_rules():
    """rate_workout_system() prompt must include the follow-up dialogue instructions."""
    from services.prompts import rate_workout_system

    prompt = rate_workout_system()

    # Must mention the new structured fields
    assert "needs_athlete_feedback" in prompt
    assert "follow_up_question" in prompt
    assert "suggested_feedback_tags" in prompt

    # Must describe when to ask a follow-up (short/low-confidence/ambiguous)
    assert "short" in prompt.lower()
    assert "low-confidence" in prompt.lower() or "ambiguous" in prompt.lower()


def test_rate_workout_system_prompt_backward_compatible_fields():
    """rate_workout_system() must still mention feedback and flag_for_adaptation fields."""
    from services.prompts import rate_workout_system

    prompt = rate_workout_system()
    assert '"feedback"' in prompt
    assert '"flag_for_adaptation"' in prompt


@pytest.mark.asyncio
async def test_rate_completed_workout_returns_follow_up_fields_for_ambiguous_ride():
    """For a short/ambiguous ride the AI may signal needs_athlete_feedback=true."""
    ai_response = {
        "feedback": "This looks like a short easy spin rather than a full endurance session.",
        "flag_for_adaptation": False,
        "needs_athlete_feedback": True,
        "follow_up_question": "Was this intentional recovery, a commute, or did you cut it short?",
        "suggested_feedback_tags": ["recovery", "commute", "cut_short"],
    }

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        import json as _json
        return _json.dumps(ai_response)

    day = {
        "workoutType": "endurance",
        "title": "Long Ride",
        "description": "2-hour endurance ride",
        "durationMinutes": 120,
        "feedback": {"actualDurationMinutes": 20, "perceivedEffort": 1, "notes": ""},
    }

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        result = await ai_service.rate_completed_workout(day, {})

    assert result["needs_athlete_feedback"] is True
    assert result["follow_up_question"] == "Was this intentional recovery, a commute, or did you cut it short?"
    assert result["suggested_feedback_tags"] == ["recovery", "commute", "cut_short"]


@pytest.mark.asyncio
async def test_rate_completed_workout_returns_no_follow_up_for_normal_ride():
    """For a normal high-confidence ride the follow-up fields should be falsy/empty."""
    ai_response = {
        "feedback": "Great endurance session — you held Z2 power consistently for 90 minutes.",
        "flag_for_adaptation": False,
        "needs_athlete_feedback": False,
        "follow_up_question": None,
        "suggested_feedback_tags": [],
    }

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        import json as _json
        return _json.dumps(ai_response)

    day = {
        "workoutType": "endurance",
        "title": "Long Ride",
        "description": "90-min Z2",
        "durationMinutes": 90,
        "feedback": {"actualDurationMinutes": 90, "perceivedEffort": 2, "notes": "Felt solid"},
    }

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        result = await ai_service.rate_completed_workout(day, {})

    assert result["needs_athlete_feedback"] is False
    assert result["follow_up_question"] is None
    assert result["suggested_feedback_tags"] == []
    assert "Great endurance" in result["feedback"]


@pytest.mark.asyncio
async def test_rate_completed_workout_defaults_missing_follow_up_fields():
    """If the AI response omits the new fields, sensible defaults are returned."""
    # Legacy AI response without the new fields
    ai_response = {
        "feedback": "Good session.",
        "flag_for_adaptation": False,
    }

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        import json as _json
        return _json.dumps(ai_response)

    day = {
        "workoutType": "endurance",
        "title": "Ride",
        "description": "Easy",
        "durationMinutes": 60,
        "feedback": {"actualDurationMinutes": 60, "perceivedEffort": 2, "notes": ""},
    }

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        result = await ai_service.rate_completed_workout(day, {})

    assert result["needs_athlete_feedback"] is False
    assert result["follow_up_question"] is None
    assert result["suggested_feedback_tags"] == []


# ---------------------------------------------------------------------------
# Task 10: Tighten prompts around natural coaching behavior
# ---------------------------------------------------------------------------


def test_rate_workout_system_includes_response_quality_rules():
    """rate_workout_system must include response quality rules for the feedback field."""
    from services.prompts import rate_workout_system

    prompt = rate_workout_system().lower()

    # Must instruct the model to acknowledge uncertainty
    assert "uncertainty" in prompt or "uncertain" in prompt or "fabricate" in prompt
    # Must require a concrete ride detail in the response
    assert "concrete" in prompt or "specific" in prompt
    # Must require a clear next action
    assert "next" in prompt and ("action" in prompt or "step" in prompt)
    # Must limit follow-up questions to one
    assert "at most one" in prompt or "one follow-up" in prompt or "one concise" in prompt
    # Must guard against false endurance claims for short rides
    assert "20 minutes" in prompt or "under 20" in prompt


def test_rate_workout_system_includes_example_short_recovery_spin():
    """rate_workout_system must contain an example for a short recovery spin."""
    from services.prompts import rate_workout_system

    prompt = rate_workout_system().lower()
    assert "short recovery spin" in prompt or "leg-loosener" in prompt


def test_rate_workout_system_includes_example_over_paced_endurance():
    """rate_workout_system must contain an example for an over-paced endurance ride."""
    from services.prompts import rate_workout_system

    prompt = rate_workout_system().lower()
    assert "over-paced endurance" in prompt or ("z3/z4" in prompt and "endurance" in prompt)


def test_rate_workout_system_includes_example_missed_aborted_workout():
    """rate_workout_system must contain an example for a missed or aborted workout."""
    from services.prompts import rate_workout_system

    prompt = rate_workout_system().lower()
    assert "missed" in prompt or "aborted" in prompt


def test_rate_workout_system_includes_example_successful_interval_day():
    """rate_workout_system must contain an example for a successful interval day."""
    from services.prompts import rate_workout_system

    prompt = rate_workout_system().lower()
    assert "successful interval" in prompt or ("threshold" in prompt and "target" in prompt and "solid" in prompt)


def test_rate_workout_system_includes_example_over_paced_endurance():
    """rate_workout_system must contain an example for an over-paced endurance ride."""
    from services.prompts import rate_workout_system

    prompt = rate_workout_system().lower()
    assert "over-paced endurance" in prompt or ("z3/z4" in prompt and "endurance" in prompt)


def test_rate_workout_system_includes_example_missed_aborted_workout():
    """rate_workout_system must contain an example for a missed or aborted workout."""
    from services.prompts import rate_workout_system

    prompt = rate_workout_system().lower()
    assert "missed" in prompt or "aborted" in prompt


def test_rate_workout_system_includes_example_successful_interval_day():
    """rate_workout_system must contain an example for a successful interval day."""
    from services.prompts import rate_workout_system

    prompt = rate_workout_system().lower()
    assert "successful interval" in prompt or ("threshold" in prompt and "target" in prompt and "solid" in prompt)


# ---------------------------------------------------------------------------
# Outlook feature (Task 7)
# ---------------------------------------------------------------------------


def test_ask_trainer_system_includes_outlook_rules():
    """The ask_trainer system prompt must contain outlook handling instructions."""
    from services.prompts import ask_trainer_system, ask_trainer_plan_updates_rule

    prompt = ask_trainer_system(
        profile={"name": "Alice"},
        today="2026-05-01",
        last_7_days=[],
        next_n_days=[],
        assessment_section="",
        memory_section="",
        workout_section="",
        plan_updates_rule=ask_trainer_plan_updates_rule(None),
    )

    # The prompt must mention outlook-related guidance
    assert "outlook" in prompt.lower()
    # Must reference next 3-5 sessions
    assert "3-5" in prompt
    # Must state that planUpdates should be omitted for a plain outlook
    assert "planUpdates" in prompt or "plan_updates" in prompt.lower()


@pytest.mark.asyncio
async def test_ask_trainer_outlook_prompt_contains_outlook_rules():
    """When ask_trainer is called, the system prompt passed to the LLM contains outlook rules."""
    captured_prompt: list[str] = []

    async def fake_chat_history(provider, system_prompt, messages, json_mode=False, **kwargs):
        captured_prompt.append(system_prompt)
        return json.dumps({"response": "Here are your next sessions.", "sources": []})

    with patch.object(ai_service, "_chat_history", side_effect=fake_chat_history):
        await ai_service.ask_trainer(
            question="Show me an outlook for my next few sessions",
            plan=PLAN_FOR_LOAD_TESTS,
            profile=PROFILE_WITH_FTP,
        )

    assert len(captured_prompt) >= 1
    assert "outlook" in captured_prompt[0].lower()
    assert "3-5" in captured_prompt[0]


@pytest.mark.asyncio
async def test_ask_trainer_outlook_no_plan_updates_when_ai_omits_them():
    """Asking for an outlook that does not include planUpdates should return no plan updates."""

    async def fake_chat_history(provider, system_prompt, messages, json_mode=False, **kwargs):
        # AI returns an outlook response with no planUpdates
        return json.dumps({
            "response": "Next up: endurance on Wed, intervals Thu, long ride Sat.",
            "sources": [],
        })

    with patch.object(ai_service, "_chat_history", side_effect=fake_chat_history):
        result = await ai_service.ask_trainer(
            question="Show me an outlook for my next few sessions",
            plan=PLAN_FOR_LOAD_TESTS,
            profile=PROFILE_WITH_FTP,
        )

    # plan_updates must be absent / empty when the AI does not return them
    assert result.get("plan_updates") is None or result.get("plan_updates") == []
    assert "endurance" in result["response"].lower() or "next" in result["response"].lower()


def test_ask_trainer_system_includes_response_quality_rules():
    """ask_trainer_system must include response quality rules for the response field."""
    from services.prompts import ask_trainer_system, ask_trainer_plan_updates_rule

    prompt = ask_trainer_system(
        profile={"name": "Alice"},
        today="2026-05-01",
        last_7_days=[],
        next_n_days=[],
        assessment_section="",
        memory_section="",
        workout_section="",
        plan_updates_rule=ask_trainer_plan_updates_rule(None),
    ).lower()

    # Must acknowledge uncertainty when data is weak
    assert "uncertain" in prompt or "sparse" in prompt or "weak" in prompt
    # Must require reference to a concrete ride/athlete detail
    assert "concrete" in prompt or "specific" in prompt
    # Must require a clear next action
    assert "next action" in prompt or "next step" in prompt or "recommendation" in prompt
    # Must cap follow-up questions at one
    assert "at most one" in prompt or "one follow-up" in prompt or "one concise" in prompt


def test_ask_trainer_system_includes_outlook_response_example():
    """ask_trainer_system must include an example response for an outlook request."""
    from services.prompts import ask_trainer_system, ask_trainer_plan_updates_rule

    prompt = ask_trainer_system(
        profile={"name": "Alice"},
        today="2026-05-01",
        last_7_days=[],
        next_n_days=[],
        assessment_section="",
        memory_section="",
        workout_section="",
        plan_updates_rule=ask_trainer_plan_updates_rule(None),
    ).lower()

    # The prompt should contain an inline example for outlook responses
    assert "example" in prompt
    # The example should reference sessions and fatigue/tsb in context
    assert "recovery" in prompt or "endurance" in prompt
    assert "tsb" in prompt.lower() or "fatigue" in prompt.lower()


# ---------------------------------------------------------------------------
# Task 8: Improved coach memory – update_memory_system prompt categories
# ---------------------------------------------------------------------------


def test_update_memory_system_includes_schedule_constraints():
    """System prompt must guide the AI to capture schedule constraints."""
    from services.prompts import update_memory_system

    text = update_memory_system().lower()
    assert "schedule" in text or "weekday" in text or "availability" in text


def test_update_memory_system_includes_fatigue_patterns():
    """System prompt must guide the AI to capture subjective fatigue / intensity response."""
    from services.prompts import update_memory_system

    text = update_memory_system().lower()
    assert "fatigue" in text or "intensity" in text or "over-reaching" in text


def test_update_memory_system_includes_preferred_workout_types():
    """System prompt must guide the AI to capture preferred workout types."""
    from services.prompts import update_memory_system

    text = update_memory_system().lower()
    assert "preferred workout" in text or "workout types" in text or "favourite" in text or "favorite" in text


def test_update_memory_system_includes_recurring_issues():
    """System prompt must guide the AI to capture recurring coaching issues."""
    from services.prompts import update_memory_system

    text = update_memory_system().lower()
    assert "recurring" in text or "over-pacing" in text or "repeated" in text


def test_update_memory_system_includes_ftp_context():
    """System prompt must guide the AI to record FTP history."""
    from services.prompts import update_memory_system

    text = update_memory_system().lower()
    assert "ftp" in text


def test_update_memory_system_includes_race_event_priorities():
    """System prompt must guide the AI to capture race and event priorities."""
    from services.prompts import update_memory_system

    text = update_memory_system().lower()
    assert "race" in text or "event" in text or "priorities" in text


def test_update_memory_system_avoids_transient_details():
    """System prompt must explicitly instruct the AI not to store transient / one-off details."""
    from services.prompts import update_memory_system

    text = update_memory_system().lower()
    assert "transient" in text or "one-off" in text or "durable" in text


def test_update_memory_system_uses_coach_persona():
    """update_memory_system must embed the COACH_PERSONA."""
    from services.prompts import update_memory_system, COACH_PERSONA

    assert COACH_PERSONA in update_memory_system()


def test_update_memory_user_includes_existing_notes_and_exchange():
    """update_memory_user must include existing notes and the latest exchange."""
    from services.prompts import update_memory_user

    msg = update_memory_user(
        current_memory="Prefers morning rides.",
        user_message="I can only ride 45 min on weekdays.",
        coach_response="Noted, I'll keep weekday sessions short.",
    )
    assert "Prefers morning rides." in msg
    assert "45 min on weekdays" in msg
    assert "weekday sessions short" in msg


def test_update_memory_user_handles_empty_memory():
    """update_memory_user must not raise when current_memory is empty."""
    from services.prompts import update_memory_user

    msg = update_memory_user(current_memory="", user_message="Hi", coach_response="Hello!")
    assert "none" in msg.lower() or msg  # at minimum it must return a non-empty string


@pytest.mark.asyncio
async def test_update_coach_memory_passes_system_and_user_prompts():
    """update_coach_memory must call _chat with the system and user prompts."""
    from services.prompts import update_memory_system, update_memory_user

    captured: list[tuple[str, str]] = []

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        captured.append((system_prompt, user_msg))
        return "Schedule constraints: weekdays limited to 45 min."

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        result = await ai_service.update_coach_memory(
            current_memory="",
            user_message="I can only ride 45 min on weekdays.",
            coach_response="Noted.",
            provider="openai",
        )

    assert len(captured) == 1
    system_prompt, user_msg = captured[0]
    assert system_prompt == update_memory_system()
    assert "45 min on weekdays" in user_msg
    assert "weekdays" in result.lower() or "45 min" in result.lower()
