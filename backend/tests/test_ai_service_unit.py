"""Unit tests for services/ai_service.py helper functions."""

from __future__ import annotations

import json
import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import services.ai_service as ai_service
import services.analysis as analysis


def test_process_pending_feedbacks_prompt_uses_listed_activity_as_authoritative():
    from services.prompts import process_pending_feedbacks_system, process_pending_feedbacks_user

    system_prompt = process_pending_feedbacks_system()
    user_prompt = process_pending_feedbacks_user(
        rides=[
            SimpleNamespace(
                activity_name="Darmstadt Mountain Biking",
                activity_date="2026-06-11",
                sport_type="MountainBikeRide",
                duration_seconds=6 * 3600 + 14 * 60,
                tss=120.0,
                plan_match_status="auto_matched",
                matched_plan_snapshot={"title": "Complete Rest Day"},
            ),
            SimpleNamespace(
                activity_name="Oberursel (Taunus) Mountain Biking",
                activity_date="2026-06-14",
                sport_type="MountainBikeRide",
                duration_seconds=4 * 3600 + 13 * 60,
                tss=180.0,
                plan_match_status="auto_matched",
                matched_plan_snapshot={"title": "Complete Rest Day"},
            )
        ],
        assessment={
            "notes": "Older summary says the most recent activity was Mittelberg Hiking."
        },
        training_plan=[],
        timezone_name="Europe/Berlin",
    )

    assert "authoritative basis" in system_prompt
    assert "first bullet must summarize the latest listed activity" in system_prompt
    assert (
        "Latest listed activity (anchor the first summary bullet on this activity): "
        "Name: Oberursel (Taunus) Mountain Biking"
    ) in user_prompt
    assert "Name: Oberursel (Taunus) Mountain Biking" in user_prompt
    assert "Type: MountainBikeRide" in user_prompt
    assert "Mittelberg Hiking" not in user_prompt
    assert "prefer the listed activity data" in system_prompt
    assert "Ignore older free-form assessment notes" in user_prompt
    assert "the first bullet must mention the latest listed activity by name." in user_prompt


@pytest.mark.asyncio
async def test_generate_summary_from_ride_feedbacks_falls_back_when_llm_summary_is_empty():
    rides = [
        SimpleNamespace(
            activity_name="Mittelberg Hiking",
            activity_date="2026-06-11",
            sport_type="Hike",
            duration_seconds=6 * 3600 + 14 * 60,
            tss=120.0,
            ctl_after=53.8,
            atl_after=32.0,
            tsb_after=21.8,
            matched_plan_snapshot={"title": "Complete Rest Day"},
        ),
        SimpleNamespace(
            activity_name="Oberursel (Taunus) Mountain Biking",
            activity_date="2026-06-14",
            sport_type="MountainBikeRide",
            duration_seconds=4 * 3600 + 13 * 60,
            tss=180.0,
            ctl_after=51.3,
            atl_after=35.5,
            tsb_after=15.8,
            matched_plan_snapshot={"title": "Complete Rest Day"},
        ),
    ]

    with patch.object(ai_service, "_chat", return_value='{"loginSummary": ""}'):
        summary = await ai_service.generate_summary_from_ride_feedbacks(
            rides=rides,
            assessment={"notes": "Older summary says Mittelberg Hiking was the latest."},
            training_plan=[],
            provider="openai",
        )

    assert ai_service._is_complete_login_summary(summary)
    assert "Oberursel (Taunus) Mountain Biking" in summary
    assert "Mittelberg Hiking" not in summary


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


def test_parse_ai_json_logs_warning_when_repair_alters_input(caplog):
    import logging

    # Truncated JSON — repair_json will close the open string and add missing braces
    raw = '{"description": "Zone 2 ride with focus on cade'
    with caplog.at_level(logging.WARNING, logger="services.token_accounting"):
        ai_service._parse_ai_json(raw)
    # Reported through token_accounting so the line carries the task/model/
    # prompt_sha of the call that produced the bad JSON (#516).
    assert any("LLM json_repair" in r.getMessage() for r in caplog.records)


def test_parse_ai_json_does_not_log_when_input_is_valid(caplog):
    import logging

    raw = '{"key": "value"}'
    with caplog.at_level(logging.WARNING, logger="services.token_accounting"):
        ai_service._parse_ai_json(raw)
    assert not any("LLM json_repair" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# _parse_ai_json — the unit stripper stays out of the prose (#558)
# ---------------------------------------------------------------------------


def test_parse_ai_json_strips_a_unit_written_into_a_numeric_field():
    """The behaviour #516 added, and the only one the stripper is allowed."""
    raw = '{"durationMinutes": 180 minutes, "targetPower": 250 watts}'

    result = ai_service._parse_ai_json(raw)

    assert result["durationMinutes"] == 180
    assert result["targetPower"] == 250


@pytest.mark.parametrize(
    "sentence",
    [
        # Each of these was verified to be damaged by the old document-wide
        # regex: a number, a word, then a comma. A unit before the closing quote
        # of a string was always safe, so no such case is listed here — it would
        # pass with or without the fix.
        "Wir fahren am Samstag 4 Stunden, danach Pause.",
        "Das waren 3 Wochen, und jetzt kommt die Erholung.",
        "Ride for 2 hours, then recover.",
    ],
)
def test_parse_ai_json_leaves_the_coachs_prose_intact(sentence):
    """The athlete used to read "am Samstag 4, danach Pause" (#558)."""
    result = ai_service._parse_ai_json(json.dumps({"response": sentence}))

    assert result["response"] == sentence


def test_parse_ai_json_keeps_a_unit_before_a_literal_newline_in_prose():
    """Models emit unescaped newlines inside strings; repair_json fixes those.

    The old regex fired on the newline first and dropped the unit on the way.
    """
    raw = '{"response": "Sonntag 3 Stunden\nMontag frei"}'

    assert ai_service._parse_ai_json(raw)["response"] == "Sonntag 3 Stunden\nMontag frei"


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


def test_plan_updates_rule_defers_on_provisional_classification():
    """The coach must treat unknown/low-confidence ride classifications as provisional
    and defer to the athlete's firsthand account rather than asserting the type (#409)."""
    from services.prompts import ask_trainer_plan_updates_rule

    rule = ask_trainer_plan_updates_rule(None)
    lowered = rule.lower()
    assert "provisional" in lowered
    # Must instruct deferring to the athlete's stated session over the auto-guess.
    assert "firsthand account" in lowered
    # Must forbid re-labelling a reported session as something else (the observed bug).
    assert "unknown" in lowered and "confidence" in lowered


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
async def test_analyse_strava_activities_grounds_time_in_zone_from_streams():
    """Streams + entered FTP produce a grounded time-in-zone line in the prompt (#468)."""
    activities = [
        {
            "id": 10,
            "name": "Endurance ride",
            "type": "Ride",
            "distance": 50000,
            "movingTime": 3600,
            "startDate": "2026-07-25T08:00:00Z",
            # snake_case power fields (as router's model_dump() emits) so the
            # labeled block — where the time-in-zone line is appended — renders.
            "average_watts": 200,
            "weighted_average_watts": 205,
        }
    ]
    # A flat ~200 W stream at FTP 320 is squarely Zone 2 (176–240 W).
    streams_by_id = {
        "10": {
            "watts": {"data": [200.0] * 10},
            "time": {"data": [float(i * 30) for i in range(10)]},
        }
    }

    ai_response = json.dumps(
        {
            "riderType": "endurance",
            "notes": "Solid aerobic base.",
            "rideInsights": "Endurance ride.",
            "lastRideFeedback": "Nice steady effort.",
        }
    )
    captured: dict[str, str] = {}

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        captured["user_msg"] = user_msg
        return ai_response

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        await ai_service.analyse_strava_activities(
            activities,
            provider="openai",
            streams_by_id=streams_by_id,
            user_ftp=320,
        )

    msg = captured["user_msg"]
    # Zone boundaries with the Z2 ceiling, and measured time-in-zone (all in Z2).
    assert "Zone 2 / endurance ceiling = 240 W" in msg
    assert "time in zones: Z2" in msg


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
async def test_generate_training_plan_repairs_invalid_day():
    """A day that fails PlanDay validation triggers a re-prompt, then succeeds (#422)."""
    profile = {"bikeType": "road", "trainingGoal": "general_fitness",
               "fitnessLevel": "intermediate"}
    invalid = {"date": "2026-04-10", "workoutType": "endurance", "title": "x",
               "description": "y", "durationMinutes": "lots"}  # non-numeric duration
    valid = [{"date": "2026-04-10", "workoutType": "endurance", "title": "x",
              "description": "y", "durationMinutes": 90}]
    prompts: list[str] = []

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        prompts.append(user_msg)
        payload = {"plan": [invalid]} if len(prompts) == 1 else {"plan": valid}
        return json.dumps(payload)

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        result = await ai_service.generate_training_plan(profile, provider="openai")

    assert len(prompts) == 2  # retried once
    assert result[0]["durationMinutes"] == 90
    assert "invalid" in prompts[1].lower()  # correction fed back to the model


@pytest.mark.asyncio
async def test_generate_training_plan_gives_up_after_max_attempts():
    """After the repair budget is exhausted the caller gets a format error, not a 500."""
    profile = {"bikeType": "road", "trainingGoal": "general_fitness",
               "fitnessLevel": "intermediate"}
    invalid = {"date": "2026-04-10", "durationMinutes": "lots"}
    calls = 0

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        nonlocal calls
        calls += 1
        return json.dumps({"plan": [invalid]})

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        with pytest.raises(ai_service.AIResponseFormatError):
            await ai_service.generate_training_plan(profile, provider="openai")

    assert calls == ai_service._MAX_PLAN_VALIDATION_ATTEMPTS


@pytest.mark.asyncio
async def test_match_observations_to_recommendations_routes_by_llm():
    """LLM matcher routes each observation to its assigned recommendation index."""
    recs = [
        {"recommendation": "Prioritise recovery rides."},
        {"recommendation": "Add one long endurance ride."},
    ]
    observations = [
        "Athlete overreaches when feeling fresh.",
        "Tends to skip long rides.",
    ]

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        return json.dumps(
            {
                "assignments": [
                    {"observationIndex": 0, "recommendationIndex": 0},
                    {"observationIndex": 1, "recommendationIndex": 1},
                ]
            }
        )

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        result = await ai_service.match_observations_to_recommendations(
            recs, observations, provider="openai"
        )

    assert result == {
        0: ["Athlete overreaches when feeling fresh."],
        1: ["Tends to skip long rides."],
    }


@pytest.mark.asyncio
async def test_match_observations_omitted_or_null_falls_back_to_primary():
    """Observations the model omits or marks irrelevant land on the primary rec."""
    recs = [{"recommendation": "A"}, {"recommendation": "B"}]
    observations = ["relevant to B", "irrelevant", "never mentioned"]

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        return json.dumps(
            {
                "assignments": [
                    {"observationIndex": 0, "recommendationIndex": 1},
                    {"observationIndex": 1, "recommendationIndex": None},
                    # observationIndex 2 omitted entirely
                ]
            }
        )

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        result = await ai_service.match_observations_to_recommendations(
            recs, observations, provider="openai"
        )

    assert result[1] == ["relevant to B"]
    # Irrelevant + omitted both fall back to the primary recommendation, in order.
    assert result[0] == ["irrelevant", "never mentioned"]


@pytest.mark.asyncio
async def test_match_observations_empty_inputs_skip_llm():
    """No observations or recommendations → no LLM call, empty assignments."""
    with patch.object(ai_service, "_chat", side_effect=AssertionError("should not call")):
        assert await ai_service.match_observations_to_recommendations([], ["x"]) == {}
        assert (
            await ai_service.match_observations_to_recommendations([{"recommendation": "A"}], [])
            == {}
        )


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
# ask_trainer — a reply that ignores the JSON contract (#558)
#
# Production, three requests in a row, same question: the model answered in
# German prose, repair_json emptied it, json.loads raised, and the router — which
# has no handler for JSONDecodeError — returned 500 with a traceback. The
# athlete's question is not persisted on a failed request, so the turn was gone.
# ---------------------------------------------------------------------------


PROSE_REPLY = (
    "Das klingt gut! Sonntag 2-3 Stunden Grundlage passt gut ins Wochenende, "
    "solange du Samstag nicht überziehst."
)


@pytest.mark.asyncio
async def test_ask_trainer_returns_a_prose_reply_as_the_answer():
    calls: list[str] = []

    async def fake_chat_history(provider, system_prompt, messages, json_mode=False, **kwargs):
        calls.append(kwargs.get("task", ""))
        return PROSE_REPLY

    with patch.object(ai_service, "_chat_history", side_effect=fake_chat_history):
        result = await ai_service.ask_trainer(
            question="vllt gehn sonntag ja noch 2-3h grundlage?",
            plan=PLAN_FOR_LOAD_TESTS,
            profile=PROFILE_WITH_FTP,
        )

    assert result["response"] == PROSE_REPLY
    # Prose carries no structure, so there is nothing to write to the plan.
    assert not result["plan_updates"]
    assert result["sources"] == []
    # And it costs exactly one call: the same prompt produced prose three times
    # out of three in production, so retrying only buys another 17k input tokens.
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_ask_trainer_logs_the_prose_reply_by_shape_and_never_by_content(caplog):
    """The reply is the athlete's health data — sizes may be logged, text may not (#499)."""
    import logging

    async def fake_chat_history(provider, system_prompt, messages, json_mode=False, **kwargs):
        return PROSE_REPLY

    with patch.object(ai_service, "_chat_history", side_effect=fake_chat_history):
        with caplog.at_level(logging.WARNING, logger="services.ai_service"):
            await ai_service.ask_trainer(
                question="und sonntag?",
                plan=PLAN_FOR_LOAD_TESTS,
                profile=PROFILE_WITH_FTP,
            )

    line = next(
        r.getMessage() for r in caplog.records if "outside the JSON contract" in r.getMessage()
    )
    assert f"chars={len(PROSE_REPLY)}" in line
    assert "Grundlage" not in line
    assert "Sonntag" not in line


@pytest.mark.asyncio
async def test_ask_trainer_raises_the_format_error_when_the_reply_holds_no_answer():
    """Structure-less *and* wordless: that is the retry path, ending in a 502.

    ``AIResponseFormatError`` is what ``routers/ai.py`` already turns into a 502
    with an explanation; a bare ``JSONDecodeError`` is what it 500s on.
    """

    async def fake_chat_history(provider, system_prompt, messages, json_mode=False, **kwargs):
        return "{{{"

    with patch.object(ai_service, "_chat_history", side_effect=fake_chat_history):
        with patch.object(ai_service.asyncio, "sleep", new=AsyncMock()):
            with pytest.raises(ai_service.AIResponseFormatError):
                await ai_service.ask_trainer(
                    question="und sonntag?",
                    plan=PLAN_FOR_LOAD_TESTS,
                    profile=PROFILE_WITH_FTP,
                )


@pytest.mark.asyncio
async def test_ask_trainer_still_reads_plan_updates_out_of_a_valid_reply():
    """The fallback must not swallow the structured path it sits next to."""

    async def fake_chat_history(provider, system_prompt, messages, json_mode=False, **kwargs):
        return json.dumps({
            "response": "Samstag 4 Stunden, danach Pause.",
            "planUpdates": [{"date": "2026-04-16", "durationMinutes": 240}],
            "sources": ["a"],
        })

    with patch.object(ai_service, "_chat_history", side_effect=fake_chat_history):
        result = await ai_service.ask_trainer(
            question="samstag lang?",
            plan=PLAN_FOR_LOAD_TESTS,
            profile=PROFILE_WITH_FTP,
        )

    assert result["plan_updates"] == [{"date": "2026-04-16", "durationMinutes": 240}]
    assert result["sources"] == ["a"]
    # The unit stripper used to turn this into "Samstag 4, danach Pause." (#558).
    assert result["response"] == "Samstag 4 Stunden, danach Pause."


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


def test_adapt_plan_system_preserves_hard_schedule_constraints():
    from services.prompts import adapt_plan_system

    system = adapt_plan_system().lower()

    assert "hard athlete constraints" in system
    assert "unavailable" in system
    assert "do not schedule training" in system
    assert "physiologically optimal" in system


def test_adapt_plan_system_includes_hard_session_spacing_rules():
    from services.prompts import adapt_plan_system

    system = adapt_plan_system().lower()

    assert "hard-session spacing rules" in system
    assert "actual activity history is authoritative" in system
    assert "inspect the recent activity history first" in system
    assert "within roughly 48 hours" in system
    assert "do not keep, recommend, or create another vo2max" in system
    assert "use planupdates" in system


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
# workoutPurpose points forwards (#623)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("builder", ["generate_plan_system", "adapt_plan_system"])
def test_workout_purpose_is_specified_as_what_the_session_earns(builder):
    """The athlete reads this heading before riding, not while auditing the plan.

    It used to be specified as "the physiological goal of this session and why it
    is placed here in the plan", with an example that recapped the day before —
    which put a justification of the planner under a heading the athlete opens
    to find out what the next hour buys them.  ``plan_allocation_rule`` already
    argued the opposite ("write what the day buys them"), so the two specs
    disagreed with each other.
    """
    from services import prompts

    text = getattr(prompts, builder)()
    purpose = text[text.index('"workoutPurpose"') :][:600].lower()

    assert "earns the athlete" in purpose
    assert "forwards" in purpose
    assert "why it is placed here" not in purpose


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
        assert "name" in text
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


# ---------------------------------------------------------------------------
# ride_metrics_context_section — prose window (#513)
# ---------------------------------------------------------------------------


def _windowed_metrics(count: int) -> list:
    """`count` rides, newest first, each carrying prose the window can drop."""

    class FakeMetric:
        def __init__(self, index: int) -> None:
            self.activity_date = f"2026-05-{30 - index:02d}"
            self.ride_purpose = "endurance"
            self.classification_confidence = "low"
            self.classification_reason = f"reason for ride {index}"
            self.tss = 80.0
            self.normalized_power_w = 190
            self.ctl_after = 55.0
            self.atl_after = 60.0
            self.tsb_after = -5.0
            self.summary = f"summary for ride {index}"
            self.coach_note = f"coach note for ride {index}"
            self.user_note = f"athlete note for ride {index}"
            self.matched_plan_snapshot = {"title": f"planned ride {index}"}

    return [FakeMetric(i) for i in range(count)]


def test_prose_window_keeps_every_ride_but_only_recent_coach_notes():
    """The window trims prose, never rides — a trend question still has its data."""
    from services.prompts import ride_metrics_context_section

    section = ride_metrics_context_section(_windowed_metrics(10), prose_window=3)

    # Every ride keeps its metrics line, including the oldest.
    for index in range(10):
        assert f"summary for ride {index}" in section
        assert f"2026-05-{30 - index:02d}" in section

    # The coach's own notes stop at the window.
    for index in range(3):
        assert f"coach note for ride {index}" in section
    for index in range(3, 10):
        assert f"coach note for ride {index}" not in section
        assert f"reason for ride {index}" not in section
        assert f"planned ride {index}" not in section


def test_prose_window_never_drops_the_athletes_own_words():
    """Coach notes are reconstructible from the data; what the athlete said is not."""
    from services.prompts import ride_metrics_context_section

    section = ride_metrics_context_section(_windowed_metrics(10), prose_window=3)

    for index in range(10):
        assert f"athlete note for ride {index}" in section


def test_prose_window_defaults_to_the_full_history():
    """The analysis paths pass no window and must see exactly what they saw before."""
    from services.prompts import ride_metrics_context_section

    metrics = _windowed_metrics(10)
    assert ride_metrics_context_section(metrics) == ride_metrics_context_section(
        metrics, prose_window=None
    )
    assert "coach note for ride 9" in ride_metrics_context_section(metrics)


def test_prose_window_larger_than_the_history_changes_nothing():
    from services.prompts import ride_metrics_context_section

    metrics = _windowed_metrics(4)
    assert ride_metrics_context_section(
        metrics, prose_window=99
    ) == ride_metrics_context_section(metrics)


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
        tss_source: str | None = "power",
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
        self.tss_source = tss_source
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
    # The linkage is phrased, never emitted as the bare enum: the coach read
    # "plan match:manual_matched" as a verdict on execution (#551).
    assert "plan linkage:linked to a planned session by the athlete" in section
    assert "manual_matched" not in section
    assert "Planned workout: Planned Tempo, 80 min" in section


def test_ride_metrics_context_section_includes_duration_and_display_label():
    from services.prompts import ride_metrics_context_section

    class FakeMetric:
        activity_date = "2026-06-20"
        ride_purpose = "endurance"
        sport_type = "cycling"
        classification_confidence = "high"
        classification_reason = None
        duration_seconds = 205 * 60
        tss = 140
        normalized_power_w = 210
        ctl_after = 55.0
        atl_after = 58.0
        tsb_after = -3.0
        summary = "Long endurance ride"
        coach_note = None
        user_note = None
        plan_match_status = "auto_matched"
        matched_plan_date = "2026-06-20"
        matched_plan_snapshot = {
            "title": "Long Endurance Ride with Climbing Focus",
            "durationMinutes": 180,
        }
        label_override = "Close"

    section = ride_metrics_context_section([FakeMetric()])
    assert "duration 205 min" in section
    # An explicit label is reported as the badge the athlete is looking at, and
    # marked as outranking anything computed from the plan (#551).
    assert 'badge:"Close" (set explicitly, overrides the computed badge)' in section
    assert "Planned workout: Long Endurance Ride with Climbing Focus, 180 min" in section


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
    assert "Plan linkage: linked to a planned session, but which one is uncertain" in msg
    assert "ambiguous" not in msg
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


def test_rate_workout_user_flags_low_confidence_classification():
    """A low/medium-confidence category must be surfaced as uncertain, with an
    instruction to ask the athlete rather than narrate it as fact."""
    from services.prompts import rate_workout_user

    day = {
        "workoutType": "intervals",
        "title": "VO2max 4x4",
        "description": "4x4 min VO2max",
        "durationMinutes": 75,
    }
    analysis = {
        "ride_category": "tempo",
        "classification_confidence": "medium",
        "classification_reason": "Average power in tempo band with no distinct "
        "interval blocks detected.",
        "avg_power_w": 242,
        "intervals_detected": [],
    }
    prompt = rate_workout_user(day, feedback={}, actual_ride_analysis=analysis)

    assert "Classification confidence: medium" in prompt
    assert "no distinct interval blocks" in prompt
    # Must tell the coach not to assert the category and to ask the athlete.
    assert "not certain" in prompt
    assert "ask them what they actually did" in prompt


def test_rate_workout_user_omits_uncertainty_note_for_high_confidence():
    """A high-confidence classification should not carry the 'ask the athlete' note."""
    from services.prompts import rate_workout_user

    day = {"workoutType": "intervals", "title": "VO2max 4x4", "durationMinutes": 75}
    analysis = {
        "ride_category": "interval_vo2max",
        "classification_confidence": "high",
        "classification_reason": "Structured vo2max intervals detected.",
        "avg_power_w": 242,
        "intervals_detected": [
            {"duration_secs": 240, "avg_power_w": 360, "power_pct_ftp": 112}
        ],
    }
    prompt = rate_workout_user(day, feedback={}, actual_ride_analysis=analysis)

    assert "Classification confidence: high" in prompt
    assert "not certain" not in prompt
    assert "ask them what they actually did" not in prompt


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


def test_athlete_context_section_omits_empty_defaults():
    from services.prompts import athlete_context_section

    assert athlete_context_section(None) == ""
    assert (
        athlete_context_section(
            {
                "trainingTendency": "unknown",
                "coachingRisks": [],
                "notes": "",
            }
        )
        == ""
    )


def test_ask_trainer_system_includes_structured_athlete_context():
    from services.prompts import ask_trainer_plan_updates_rule, ask_trainer_system

    prompt = ask_trainer_system(
        profile={"name": "Alice"},
        today="2026-05-01",
        last_7_days=[],
        next_n_days=[],
        assessment_section="",
        memory_section="",
        workout_section="",
        plan_updates_rule=ask_trainer_plan_updates_rule(None),
        athlete_context={
            "trainingTendency": "overtrains",
            "restResponse": "restless",
            "adherencePattern": "adds_extra",
            "coachingRisks": ["doing too much when fresh"],
            "notes": "Needs explicit permission to rest.",
        },
    )

    assert "Structured athlete context (durable coaching model)" in prompt
    assert '"trainingTendency": "overtrains"' in prompt
    assert '"restResponse": "restless"' in prompt
    assert "doing too much when fresh" in prompt
    assert "stable knowledge" in prompt


def test_athlete_memory_facts_section_filters_untrusted_facts():
    from services.prompts import athlete_memory_facts_section

    section = athlete_memory_facts_section(
        [
            {
                "fact": "Does too much when fresh",
                "category": "coaching_risk",
                "sourceSnippet": "Added extra intervals after rest.",
                "confidence": 0.75,
                "status": "active",
                "lastConfirmedAt": "2026-06-18T08:00:00Z",
                "observationCount": 3,
            },
            {
                "fact": "Maybe dislikes gym work",
                "category": "preference",
                "confidence": 0.2,
                "status": "active",
            },
            {
                "fact": "Only trains indoors",
                "category": "preference",
                "confidence": 0.9,
                "status": "rejected",
            },
            {
                "fact": "Uses MTB races as motivation",
                "category": "motivation",
                "confidence": 0.1,
                "status": "user_confirmed",
            },
        ]
    )

    assert "Evidence-backed athlete memory" in section
    assert "Does too much when fresh" in section
    assert "Added extra intervals after rest." in section
    assert "Uses MTB races as motivation" in section
    assert "Maybe dislikes gym work" not in section
    assert "Only trains indoors" not in section


def test_athlete_memory_facts_section_groups_facts_and_observations():
    """Stable facts and inferred observations land under distinct labels (#386)."""
    from services.prompts import athlete_memory_facts_section

    section = athlete_memory_facts_section(
        [
            {
                "fact": "FTP is about 250 W",
                "kind": "fact",
                "category": "general",
                "confidence": 0.85,
                "status": "active",
            },
            {
                "fact": "Fades in the final interval of VO2 sessions",
                "kind": "observation",
                "category": "recurring_issues",
                "confidence": 0.7,
                "status": "active",
            },
        ]
    )

    fact_label_at = section.index("Stable athlete facts")
    obs_label_at = section.index("Behavioural observations")
    # Both groups render, each under its own heading, facts first.
    assert fact_label_at < obs_label_at
    assert "FTP is about 250 W" in section[fact_label_at:obs_label_at]
    assert "Fades in the final interval" in section[obs_label_at:]


def test_athlete_memory_facts_section_defaults_missing_kind_to_observation():
    """Legacy rows without a kind are treated as observations, not facts (#386)."""
    from services.prompts import athlete_memory_facts_section

    section = athlete_memory_facts_section(
        [{"fact": "Prefers MTB", "category": "general", "confidence": 0.8, "status": "active"}]
    )

    assert "Behavioural observations" in section
    assert "Stable athlete facts" not in section


def test_ask_trainer_system_includes_athlete_memory_facts():
    from services.prompts import ask_trainer_plan_updates_rule, ask_trainer_system

    prompt = ask_trainer_system(
        profile={"name": "Alice"},
        today="2026-06-18",
        last_7_days=[],
        next_n_days=[],
        assessment_section="",
        memory_section="",
        workout_section="",
        plan_updates_rule=ask_trainer_plan_updates_rule(None),
        athlete_memory_facts=[
            {
                "fact": "Does too much when fresh",
                "category": "coaching_risk",
                "sourceSnippet": "Repeatedly added extra work after rest.",
                "confidence": 0.8,
                "status": "active",
            }
        ],
    )

    assert "Evidence-backed athlete memory" in prompt
    assert "Does too much when fresh" in prompt
    assert "Repeatedly added extra work after rest." in prompt
    assert "confidence" in prompt


def test_ask_trainer_system_includes_two_layer_recommendation_rules():
    from services.prompts import ask_trainer_plan_updates_rule, ask_trainer_system

    prompt = ask_trainer_system(
        profile={"name": "Alice"},
        today="2026-06-18",
        last_7_days=[],
        next_n_days=[],
        assessment_section="",
        memory_section="",
        workout_section="",
        plan_updates_rule=ask_trainer_plan_updates_rule(None),
    )

    assert "Recommendation reasoning layers" in prompt
    assert "Physiology layer" in prompt
    assert "Athlete-context layer" in prompt
    assert "If two options are physiologically similar" in prompt
    assert "keep the final response concise and natural" in prompt


def test_ask_trainer_system_includes_divergence_rationale_pattern():
    """Coach must surface a personal-context call when the layers diverge."""
    from services.prompts import ask_trainer_plan_updates_rule, ask_trainer_system

    prompt = ask_trainer_system(
        profile={"name": "Alice"},
        today="2026-06-18",
        last_7_days=[],
        next_n_days=[],
        assessment_section="",
        memory_section="",
        workout_section="",
        plan_updates_rule=ask_trainer_plan_updates_rule(None),
    )

    # The "numbers say X, but knowing you I would do Y" divergence pattern.
    assert "DIVERGE" in prompt
    assert "knowing how you tend to turn easy" in prompt
    # Separate structured rationale fields are part of the response contract.
    assert '"physiologyRationale"' in prompt
    assert '"contextRationale"' in prompt


def test_ask_trainer_system_allows_targeted_questions_for_ambiguous_recommendations():
    from services.prompts import ask_trainer_plan_updates_rule, ask_trainer_system

    prompt = ask_trainer_system(
        profile={"name": "Alice"},
        today="2026-06-18",
        last_7_days=[],
        next_n_days=[],
        assessment_section="",
        memory_section="",
        workout_section="",
        plan_updates_rule=ask_trainer_plan_updates_rule(None),
    )

    assert "ask exactly one short targeted learning question" in prompt
    assert "materially change the recommendation" in prompt
    assert "Do you need training stimulus today, or mainly head-clearing?" in prompt
    assert "Are you restless because you feel fresh" in prompt
    assert "Would a social ride help you more" in prompt
    assert "Do not over-ask" in prompt
    assert "make the recommendation clear" in prompt


def test_next_ride_recommendation_system_preserves_hard_constraints():
    from services.prompts import next_ride_recommendation_system

    prompt = next_ride_recommendation_system().lower()

    assert "hard athlete availability constraints" in prompt
    assert "never schedule or recommend training on a constrained" in prompt
    assert "physiologically optimal" in prompt
    assert "ask one concise clarifying question" in prompt


def test_next_ride_recommendation_system_includes_hard_session_spacing_rules():
    from services.prompts import next_ride_recommendation_system

    prompt = next_ride_recommendation_system().lower()

    assert "hard-session spacing rules" in prompt
    assert "actual activity history is authoritative" in prompt
    assert "inspect the recent activity history first" in prompt
    assert "do not keep, recommend, or create another vo2max" in prompt
    assert "within roughly 48 hours" in prompt
    assert "move the intensity to a later feasible day" in prompt


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


@pytest.mark.asyncio
async def test_ask_trainer_returns_separate_physiology_and_context_rationale():
    """The two reasoning layers are surfaced as separate structured fields."""

    async def fake_chat_history(provider, system_prompt, messages, json_mode=False, **kwargs):
        return json.dumps({
            "response": "On the numbers an easy spin is fine, but knowing you, rest today.",
            "physiologyRationale": "TSB neutral, an easy Z2 ride would be tolerable",
            "contextRationale": "tends to turn easy rides hard, so full rest protects recovery",
            "sources": [],
        })

    with patch.object(ai_service, "_chat_history", side_effect=fake_chat_history):
        result = await ai_service.ask_trainer(
            question="Can I do an easy ride today?",
            plan=PLAN_FOR_LOAD_TESTS,
            profile=PROFILE_WITH_FTP,
        )

    assert result["physiology_rationale"] == (
        "TSB neutral, an easy Z2 ride would be tolerable"
    )
    assert result["context_rationale"] == (
        "tends to turn easy rides hard, so full rest protects recovery"
    )


@pytest.mark.asyncio
async def test_ask_trainer_personal_context_overrides_metrics_recommendation():
    """Personal context can override a metrics-only recommendation."""

    async def fake_chat_history(provider, system_prompt, messages, json_mode=False, **kwargs):
        # Physiology alone would permit a ride; context drives the rest decision.
        return json.dumps({
            "response": "The numbers say a Z2 ride is okay, but knowing you I'd rest.",
            "physiologyRationale": "fresh enough for an easy ride",
            "contextRationale": "history of overreaching means rest is the safer call",
        })

    with patch.object(ai_service, "_chat_history", side_effect=fake_chat_history):
        result = await ai_service.ask_trainer(
            question="I feel restless, can I squeeze in a ride?",
            plan=PLAN_FOR_LOAD_TESTS,
            profile=PROFILE_WITH_FTP,
        )

    # The physiology layer permits a ride, but the context layer wins.
    assert "fresh" in result["physiology_rationale"]
    assert "rest" in result["context_rationale"]
    assert "rest" in result["response"].lower()


@pytest.mark.asyncio
async def test_ask_trainer_omits_blank_rationale_fields():
    """Blank rationale fields normalise to None rather than empty strings."""

    async def fake_chat_history(provider, system_prompt, messages, json_mode=False, **kwargs):
        return json.dumps({
            "response": "Easy endurance today keeps the base building.",
            "physiologyRationale": "   ",
            "sources": [],
        })

    with patch.object(ai_service, "_chat_history", side_effect=fake_chat_history):
        result = await ai_service.ask_trainer(
            question="What should I do today?",
            plan=PLAN_FOR_LOAD_TESTS,
            profile=PROFILE_WITH_FTP,
        )

    assert result["physiology_rationale"] is None
    assert result["context_rationale"] is None


@pytest.mark.asyncio
async def test_ask_trainer_rejects_empty_response_after_retries():
    call_count = 0

    async def fake_chat_history(provider, system_prompt, messages, json_mode=False, **kwargs):
        nonlocal call_count
        call_count += 1
        return json.dumps({"response": "   ", "sources": []})

    with patch.object(ai_service, "_chat_history", side_effect=fake_chat_history), \
            patch.object(ai_service.asyncio, "sleep", new=AsyncMock()):
        with pytest.raises(ai_service.AIResponseFormatError):
            await ai_service.ask_trainer(
                question="Why did the coach not answer?",
                plan=PLAN_FOR_LOAD_TESTS,
                profile=PROFILE_WITH_FTP,
            )

    # Initial attempt + configured retries.
    assert call_count == ai_service.ASK_TRAINER_EMPTY_RESPONSE_RETRIES + 1


@pytest.mark.asyncio
async def test_ask_trainer_retries_empty_response_then_succeeds():
    responses = iter([
        json.dumps({"response": "   ", "sources": []}),
        json.dumps({"response": "Here is your plan.", "sources": []}),
    ])

    async def fake_chat_history(provider, system_prompt, messages, json_mode=False, **kwargs):
        return next(responses)

    sleep_mock = AsyncMock()
    with patch.object(ai_service, "_chat_history", side_effect=fake_chat_history), \
            patch.object(ai_service.asyncio, "sleep", new=sleep_mock):
        result = await ai_service.ask_trainer(
            question="Why did the coach not answer?",
            plan=PLAN_FOR_LOAD_TESTS,
            profile=PROFILE_WITH_FTP,
        )

    assert result["response"] == "Here is your plan."
    # Backed off exactly once before the successful retry.
    sleep_mock.assert_awaited_once_with(ai_service.ASK_TRAINER_RETRY_BASE_DELAY)


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


def test_ask_trainer_system_includes_attentive_coach_rules():
    """ask_trainer_system must tell the coach to notice important athlete details."""
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

    assert "attentive coach" in prompt
    assert "high-signal" in prompt
    assert "running out of drink" in prompt
    assert "hot" in prompt
    assert "wie viel und was hast du" in prompt
    assert "same language" in prompt


def test_ask_trainer_plan_updates_rule_preserves_availability_constraints():
    from services.prompts import ask_trainer_plan_updates_rule

    rule = ask_trainer_plan_updates_rule(None).lower()

    assert "hard athlete constraints" in rule
    assert "no training on friday" in rule
    assert "do not schedule workouts on constrained" in rule
    assert "physiologically optimal" in rule


def test_ask_trainer_system_checks_constraints_before_plan_updates():
    from services.prompts import ask_trainer_system, ask_trainer_plan_updates_rule

    prompt = ask_trainer_system(
        profile={"name": "Alice"},
        today="2026-06-18",
        last_7_days=[],
        next_n_days=[
            {"date": "2026-06-18", "weekday": "Thursday", "workoutType": "intervals"},
            {"date": "2026-06-19", "weekday": "Friday", "workoutType": "rest"},
        ],
        assessment_section="",
        memory_section="Coach notes about this athlete: Friday is unavailable for training.",
        workout_section="",
        plan_updates_rule=ask_trainer_plan_updates_rule(None),
    ).lower()

    assert "hard constraint rules" in prompt
    assert "availability constraints" in prompt
    assert "do not move workouts onto constrained" in prompt
    assert "friday is unavailable for training" in prompt


def test_ask_trainer_system_includes_rest_recommendation_rules():
    from services.prompts import ask_trainer_system, ask_trainer_plan_updates_rule

    prompt = ask_trainer_system(
        profile={"name": "Alice"},
        today="2026-06-19",
        last_7_days=[],
        next_n_days=[],
        assessment_section="",
        memory_section="",
        workout_section="",
        plan_updates_rule=ask_trainer_plan_updates_rule(None),
    ).lower()

    assert "rest/recovery recommendation rules" in prompt
    assert "ctl, atl, tsb" in prompt
    assert "recent tss" in prompt
    assert "rpe" in prompt
    assert "subjective leg feel" in prompt
    assert "one easy/recovery day" in prompt
    assert "unavailable day with no training counts" in prompt
    assert "do not claim two complete rest days are mandatory" in prompt
    assert "neutral or positive tsb" in prompt
    assert "bounded easy z2/recovery ride" in prompt


def test_ask_trainer_system_rest_rules_require_numeric_explanation():
    from services.prompts import ask_trainer_system, ask_trainer_plan_updates_rule

    prompt = ask_trainer_system(
        profile={"name": "Alice"},
        today="2026-06-19",
        last_7_days=[],
        next_n_days=[],
        assessment_section="",
        memory_section="",
        workout_section="",
        plan_updates_rule=ask_trainer_plan_updates_rule(None),
    ).lower()

    assert "corrected the chronology or availability" in prompt
    assert "re-evaluate from that corrected sequence" in prompt
    assert "show the numbers" in prompt
    assert "what each number supports" in prompt
    assert "what it does not support" in prompt
    assert "generic supercompensation or overtraining language" in prompt


def test_ask_trainer_system_includes_hard_session_spacing_rules():
    from services.prompts import ask_trainer_system, ask_trainer_plan_updates_rule

    prompt = ask_trainer_system(
        profile={"name": "Alice"},
        today="2026-06-19",
        last_7_days=[
            {"date": "2026-06-18", "workoutType": "intervals", "title": "VO2max intervals"},
        ],
        next_n_days=[
            {"date": "2026-06-20", "workoutType": "intervals", "title": "VO2max intervals"},
        ],
        assessment_section="",
        memory_section="",
        workout_section="",
        plan_updates_rule=ask_trainer_plan_updates_rule(None),
        metrics_history_section=(
            "Recent activity history (newest first):\n"
            "  2026-06-18 | sport:cycling | interval_vo2max | TSS 135 | TSB +2.1"
        ),
    ).lower()

    assert "hard-session spacing rules" in prompt
    assert "actual activity history is authoritative" in prompt
    assert "do not keep, recommend, or create another vo2max" in prompt
    assert "within roughly 48 hours" in prompt
    assert "upcoming plan violates this spacing" in prompt
    assert "positive tsb" in prompt
    assert "2026-06-18 | sport:cycling | interval_vo2max" in prompt
    assert '"date": "2026-06-20"' in prompt
    assert '"title": "VO2max intervals"'.lower() in prompt


@pytest.mark.asyncio
async def test_ask_trainer_rest_prompt_handles_corrected_vo2_timing(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        ai_service, "app_today", lambda timezone_name=None: datetime.date(2026, 6, 19)
    )
    monkeypatch.setattr(
        ai_service,
        "app_date_context",
        lambda timezone_name=None: (
            "Current local date context (Europe/Berlin):\n"
            "- Today is Friday, June 19, 2026 (2026-06-19).\n"
            "- Yesterday was Thursday, June 18, 2026 (2026-06-18).\n"
            "- Tomorrow is Saturday, June 20, 2026 (2026-06-20)."
        ),
    )

    async def fake_chat_history(
        provider: str,
        system_prompt: str,
        messages: list[dict[str, str]],
        json_mode: bool = False,
        task: str = "coach",
        response_schema: dict | None = None,
    ) -> str:
        captured["system_prompt"] = system_prompt
        captured["messages"] = messages
        return json.dumps({"response": "Saturday can be easy Z2.", "sources": []})

    monkeypatch.setattr(ai_service, "_chat_history", fake_chat_history)

    await ai_service.ask_trainer(
        "I did the VO2max training yesterday because today I have no time. "
        "With CTL 56.0, ATL 53.9, TSB +2.1, TSS 135, RPE 7/10 and fresh legs, "
        "can I do a nice endurance ride tomorrow? Show the numbers.",
        plan=[
            {
                "date": "2026-06-18",
                "workoutType": "intervals",
                "title": "VO2max intervals",
                "durationMinutes": 75,
            },
            {
                "date": "2026-06-19",
                "workoutType": "rest",
                "title": "No training available",
                "durationMinutes": 0,
            },
            {
                "date": "2026-06-20",
                "workoutType": "rest",
                "title": "Complete Rest Day",
                "durationMinutes": 0,
            },
            {
                "date": "2026-06-21",
                "workoutType": "sweet_spot",
                "title": "Sweet Spot intervals",
                "durationMinutes": 90,
            },
        ],
        profile=PROFILE_WITH_FTP,
        metrics_history_section=(
            "Current ride-derived load: CTL 56.0, ATL 53.9, TSB +2.1. "
            "Yesterday: VO2max, TSS 135, RPE 7/10, legs fresh."
        ),
        timezone_name="Europe/Berlin",
    )

    system_prompt = str(captured["system_prompt"]).lower()
    assert "unavailable day with no training counts" in system_prompt
    assert "do not claim two complete rest days are mandatory" in system_prompt
    assert "neutral or positive tsb" in system_prompt
    assert "bounded easy z2/recovery ride" in system_prompt
    assert "re-evaluate from that corrected sequence" in system_prompt
    assert "show the numbers" in system_prompt
    assert "ctl 56.0, atl 53.9, tsb +2.1" in system_prompt
    assert "tss 135" in system_prompt


def test_extracts_one_off_friday_availability_constraint():
    from datetime import date

    from services.availability import extract_availability_constraints

    constraints = extract_availability_constraints(
        "Freitag habe ich keine Zeit für Training.",
        today=date(2026, 6, 18),
    )

    assert constraints == [
        {
            "constraint_type": "no_training",
            "constraint_date": "2026-06-19",
            "weekday": "friday",
            "reason": "Athlete said they are unavailable for training.",
            "source": "Freitag habe ich keine Zeit für Training.",
            "expires_on": "2026-06-19",
        }
    ]


def test_availability_constraints_block_training_plan_updates():
    from routers.ai import _filter_plan_updates_for_availability_constraints

    updates = [
        {
            "date": "2026-06-19",
            "workoutType": "intervals",
            "title": "VO2 Intervals",
            "durationMinutes": 60,
        },
        {
            "date": "2026-06-20",
            "workoutType": "endurance",
            "title": "Endurance",
            "durationMinutes": 90,
        },
    ]
    constraints = [
        {
            "constraintType": "no_training",
            "constraintDate": "2026-06-19",
            "weekday": "friday",
            "expiresOn": "2026-06-19",
        }
    ]

    filtered = _filter_plan_updates_for_availability_constraints(
        updates, constraints
    )

    assert [update["date"] for update in filtered] == ["2026-06-20"]


@pytest.mark.asyncio
async def test_ask_trainer_prompt_handles_hot_long_ride_as_high_signal():
    """A casual hot long-ride message should be sent with targeted follow-up guidance."""
    captured_prompt: list[str] = []

    async def fake_chat_history(provider, system_prompt, messages, json_mode=False, **kwargs):
        captured_prompt.append(system_prompt)
        assert "so nach dem hike" in messages[-1]["content"]
        return json.dumps(
            {
                "response": "Wie viel und was hast du unterwegs getrunken?",
                "sources": [],
            }
        )

    with patch.object(ai_service, "_chat_history", side_effect=fake_chat_history):
        result = await ai_service.ask_trainer(
            question=(
                "so nach dem hike gestern bin ich heute 5h mtb gefahren. richtig geil. "
                "nur super heiß im laufe des tages. über 30 grad. trinken war dann leer"
            ),
            plan=PLAN_FOR_LOAD_TESTS,
            profile=PROFILE_WITH_FTP,
        )

    prompt = captured_prompt[0].lower()
    assert "running out of drink" in prompt
    assert "drink volume" in prompt or "drink type" in prompt
    assert "targeted follow-up" in prompt
    assert "wie viel" in result["response"].lower()


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


def test_update_memory_system_includes_hydration_fueling_heat_context():
    """System prompt must guide the AI to retain actionable hydration/fueling context."""
    from services.prompts import update_memory_system

    text = update_memory_system().lower()
    assert "hydration" in text
    assert "fueling" in text
    assert "heat" in text
    assert "drink volume" in text
    assert "running out of fluids" in text


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


def test_update_memory_system_includes_psychological_training_tendencies():
    """System prompt must capture durable psychological training patterns."""
    from services.prompts import update_memory_system

    text = update_memory_system().lower()
    assert "psychological training tendencies" in text
    assert "overtraining" in text and "undertraining" in text
    assert "anxiety after multiple rest days" in text or "nervousness" in text
    assert "fomo" in text
    assert "overanalysis" in text
    assert "reassurance" in text
    assert "permission to rest" in text
    assert "too much when feeling fresh" in text
    assert "mtb" in text


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
    assert "single event" in text
    assert "5-hour ride" in text
    assert "one-off transient moods" in text
    assert "permanent psychological traits" in text
    assert "confirms the pattern" in text or "repeats across exchanges" in text


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
    assert "recommendation clarification question" in msg


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


# ---------------------------------------------------------------------------
# extract_athlete_facts — import historical coach conversations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_athlete_facts_returns_normalised_candidates():
    captured: list[tuple[str, str]] = []

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        captured.append((system_prompt, user_msg))
        return json.dumps({
            "candidates": [
                {
                    "fact": "Gets anxious after two rest days",
                    "category": "psychological_tendencies",
                    "confidence": 0.7,
                    "sourceSnippet": "I always feel like I'm losing fitness when I rest.",
                },
                {
                    # Confidence above the cap is clamped to 0.9.
                    "fact": "Prefers long Saturday endurance rides",
                    "category": "preferred_workouts",
                    "confidence": 1.5,
                },
            ]
        })

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        candidates = await ai_service.extract_athlete_facts(
            "Athlete: I hate resting...\nCoach: Why?", provider="openai"
        )

    assert len(candidates) == 2
    assert candidates[0]["fact"] == "Gets anxious after two rest days"
    assert candidates[0]["category"] == "psychological_tendencies"
    assert candidates[0]["confidence"] == 0.7
    assert candidates[0]["source_snippet"].startswith("I always feel")
    # Confidence is clamped to the 0.9 ceiling.
    assert candidates[1]["confidence"] == 0.9
    # The transcript reaches the model.
    assert "I hate resting" in captured[0][1]


@pytest.mark.asyncio
async def test_extract_athlete_facts_dedupes_and_drops_invalid():
    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        return json.dumps({
            "candidates": [
                {"fact": "Loves climbing", "category": "preferred_workouts", "confidence": 0.6},
                {"fact": "  loves CLIMBING ", "category": "preferred_workouts", "confidence": 0.8},
                {"fact": "", "category": "general", "confidence": 0.5},
                {"category": "general", "confidence": 0.5},
                "not a dict",
            ]
        })

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        candidates = await ai_service.extract_athlete_facts("transcript", provider="openai")

    # Duplicate (case/space-insensitive) and invalid entries are removed.
    assert len(candidates) == 1
    assert candidates[0]["fact"] == "Loves climbing"


@pytest.mark.asyncio
async def test_extract_athlete_facts_empty_transcript_skips_model():
    called = False

    async def fake_chat(*args, **kwargs):
        nonlocal called
        called = True
        return "{}"

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        candidates = await ai_service.extract_athlete_facts("   ", provider="openai")

    assert candidates == []
    assert called is False


@pytest.mark.asyncio
async def test_extract_athlete_facts_handles_malformed_payload():
    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        return json.dumps({"unexpected": "shape"})

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        candidates = await ai_service.extract_athlete_facts("transcript", provider="openai")

    assert candidates == []


# ---------------------------------------------------------------------------
# generate_athlete_insights — infer durable patterns from training history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_athlete_insights_returns_normalised_candidates():
    captured: list[tuple[str, str]] = []

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        captured.append((system_prompt, user_msg))
        return json.dumps({
            "candidates": [
                {
                    "fact": "Performs best after one recovery day",
                    "kind": "observation",
                    "category": "fatigue_response",
                    "confidence": 0.6,
                    "sourceSnippet": "Strong sessions followed rest days on 05-03 and 05-10.",
                },
                {
                    # Confidence above the cap is clamped to 0.9.
                    "fact": "Estimated FTP around 250 W",
                    "kind": "fact",
                    "category": "general",
                    "confidence": 1.4,
                },
                {
                    # A missing kind falls back to the safer "observation".
                    "fact": "Tolerates heat well",
                    "category": "general",
                    "confidence": 0.5,
                },
            ]
        })

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        candidates = await ai_service.generate_athlete_insights(
            "Recent activity history (newest first):\n  2026-05-10 | ...",
            existing_facts=["Prefers morning rides"],
            provider="openai",
        )

    assert [c["fact"] for c in candidates] == [
        "Performs best after one recovery day",
        "Estimated FTP around 250 W",
        "Tolerates heat well",
    ]
    assert [c["kind"] for c in candidates] == ["observation", "fact", "observation"]
    assert candidates[1]["confidence"] == 0.9
    # History and the existing-facts guard both reach the model.
    assert "2026-05-10" in captured[0][1]
    assert "Prefers morning rides" in captured[0][1]


@pytest.mark.asyncio
async def test_generate_athlete_insights_empty_history_skips_model():
    called = False

    async def fake_chat(*args, **kwargs):
        nonlocal called
        called = True
        return "{}"

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        candidates = await ai_service.generate_athlete_insights("   ", provider="openai")

    assert candidates == []
    assert called is False


@pytest.mark.asyncio
async def test_generate_athlete_insights_dedupes_and_caps():
    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        candidates = [
            {"fact": "Fades late in long rides", "category": "recurring_issues", "confidence": 0.5},
            {"fact": " fades LATE in long rides ", "category": "recurring_issues", "confidence": 0.7},
        ]
        candidates += [
            {"fact": f"Pattern {i}", "category": "general", "confidence": 0.4}
            for i in range(ai_service.MAX_GENERATED_INSIGHTS + 5)
        ]
        return json.dumps({"candidates": candidates})

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        candidates = await ai_service.generate_athlete_insights(
            "history", provider="openai"
        )

    facts = [c["fact"] for c in candidates]
    assert facts.count("Fades late in long rides") == 1
    assert len(candidates) == ai_service.MAX_GENERATED_INSIGHTS


@pytest.mark.asyncio
async def test_generate_athlete_insights_handles_malformed_payload():
    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        return json.dumps({"unexpected": "shape"})

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        candidates = await ai_service.generate_athlete_insights("history", provider="openai")

    assert candidates == []


# ---------------------------------------------------------------------------
# generate_athlete_hypotheses — form explicit, testable ideas from history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_athlete_hypotheses_returns_normalised_candidates():
    captured: list[tuple[str, str]] = []

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        captured.append((system_prompt, user_msg))
        return json.dumps({
            "candidates": [
                {
                    "statement": "Upper-body strength suppresses next-day HR response",
                    "category": "fatigue_response",
                    "confidence": 0.38,
                    "rationale": "HR ~8 bpm low the day after gym on 05-03 and 05-10.",
                },
                {
                    # Confidence above the hypothesis cap is clamped to 0.6.
                    "statement": "Rides stronger in the second half of a block",
                    "category": "general",
                    "confidence": 0.95,
                },
            ]
        })

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        candidates = await ai_service.generate_athlete_hypotheses(
            "Recent activity history (newest first):\n  2026-05-10 | ...",
            existing_facts=["Prefers morning rides"],
            existing_hypotheses=["Fuels poorly on long rides"],
            provider="openai",
        )

    assert [c["statement"] for c in candidates] == [
        "Upper-body strength suppresses next-day HR response",
        "Rides stronger in the second half of a block",
    ]
    assert candidates[0]["confidence"] == 0.38
    assert candidates[1]["confidence"] == 0.6
    # History, existing facts, and existing hypotheses all reach the model.
    assert "2026-05-10" in captured[0][1]
    assert "Prefers morning rides" in captured[0][1]
    assert "Fuels poorly on long rides" in captured[0][1]


@pytest.mark.asyncio
async def test_generate_athlete_hypotheses_empty_history_skips_model():
    called = False

    async def fake_chat(*args, **kwargs):
        nonlocal called
        called = True
        return "{}"

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        candidates = await ai_service.generate_athlete_hypotheses(
            "   ", provider="openai"
        )

    assert candidates == []
    assert called is False


@pytest.mark.asyncio
async def test_generate_athlete_hypotheses_dedupes_and_caps():
    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        candidates = [
            {"statement": "Fades late in long rides", "category": "recurring_issues", "confidence": 0.4},
            {"statement": " fades LATE in long rides ", "category": "recurring_issues", "confidence": 0.5},
        ]
        candidates += [
            {"statement": f"Idea {i}", "category": "general", "confidence": 0.3}
            for i in range(ai_service.MAX_GENERATED_HYPOTHESES + 5)
        ]
        return json.dumps({"candidates": candidates})

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        candidates = await ai_service.generate_athlete_hypotheses(
            "history", provider="openai"
        )

    statements = [c["statement"] for c in candidates]
    assert statements.count("Fades late in long rides") == 1
    assert len(candidates) == ai_service.MAX_GENERATED_HYPOTHESES


@pytest.mark.asyncio
async def test_generate_athlete_hypotheses_handles_malformed_payload():
    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        return json.dumps({"unexpected": "shape"})

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        candidates = await ai_service.generate_athlete_hypotheses(
            "history", provider="openai"
        )

    assert candidates == []


# ---------------------------------------------------------------------------
# generate_validation_experiments — propose experiments to resolve uncertainty
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_validation_experiments_returns_normalised_candidates():
    captured: list[tuple[str, str]] = []

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        captured.append((system_prompt, user_msg))
        return json.dumps({
            "candidates": [
                {
                    "question": "Does upper-body strength suppress next-day HR?",
                    "protocol": "Repeat the gym session, compare HR next easy ride.",
                    "rationale": "A clear HR drop confirms the hypothesis.",
                    "category": "fatigue_response",
                },
                {
                    # Missing rationale defaults to empty; category defaults.
                    "question": "Which bike is faster?",
                    "protocol": "Compare both bikes over the same climb.",
                },
            ]
        })

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        candidates = await ai_service.generate_validation_experiments(
            "Open questions:\n- upper-body strength suppresses next-day HR",
            existing_experiments=["Perform a 30-minute threshold test."],
            provider="openai",
        )

    assert [c["protocol"] for c in candidates] == [
        "Repeat the gym session, compare HR next easy ride.",
        "Compare both bikes over the same climb.",
    ]
    assert candidates[1]["rationale"] == ""
    assert candidates[1]["category"] == "general"
    # Uncertainties and already-suggested experiments both reach the model.
    assert "upper-body strength" in captured[0][1]
    assert "Perform a 30-minute threshold test." in captured[0][1]


@pytest.mark.asyncio
async def test_generate_validation_experiments_empty_context_skips_model():
    called = False

    async def fake_chat(*args, **kwargs):
        nonlocal called
        called = True
        return "{}"

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        candidates = await ai_service.generate_validation_experiments(
            "   ", provider="openai"
        )

    assert candidates == []
    assert called is False


@pytest.mark.asyncio
async def test_generate_validation_experiments_dedupes_and_caps():
    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        candidates = [
            {"question": "q", "protocol": "Repeat the VO2 session", "category": "general"},
            {"question": "q", "protocol": " repeat THE vo2 session ", "category": "general"},
        ]
        candidates += [
            {"question": "q", "protocol": f"Experiment {i}", "category": "general"}
            for i in range(ai_service.MAX_GENERATED_EXPERIMENTS + 5)
        ]
        return json.dumps({"candidates": candidates})

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        candidates = await ai_service.generate_validation_experiments(
            "uncertainty", provider="openai"
        )

    protocols = [c["protocol"] for c in candidates]
    assert protocols.count("Repeat the VO2 session") == 1
    assert len(candidates) == ai_service.MAX_GENERATED_EXPERIMENTS


@pytest.mark.asyncio
async def test_generate_validation_experiments_handles_malformed_payload():
    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        return json.dumps({"unexpected": "shape"})

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        candidates = await ai_service.generate_validation_experiments(
            "uncertainty", provider="openai"
        )

    assert candidates == []


# ---------------------------------------------------------------------------
# generate_athlete_predictions — make checkable, forward-looking predictions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_athlete_predictions_returns_normalised_candidates():
    captured: list[tuple[str, str]] = []

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        captured.append((system_prompt, user_msg))
        return json.dumps({
            "candidates": [
                {
                    "prediction": "The athlete will be fully recovered tomorrow",
                    "expectedOutcome": "Resting HR back to baseline, ready for intensity",
                    "horizon": "tomorrow",
                    "confidence": 0.7,
                    "category": "fatigue_response",
                },
                {
                    # Missing horizon defaults to empty; confidence clamps to 1.0.
                    "prediction": "Fatigue forces an easier week within 10 days",
                    "expectedOutcome": "A drop in weekly TSS",
                    "confidence": 1.4,
                },
            ]
        })

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        candidates = await ai_service.generate_athlete_predictions(
            "Recent activity history (newest first):\n  2026-05-10 | ...",
            existing_predictions=["The athlete will PR the local climb"],
            provider="openai",
        )

    assert [c["prediction"] for c in candidates] == [
        "The athlete will be fully recovered tomorrow",
        "Fatigue forces an easier week within 10 days",
    ]
    assert candidates[0]["horizon"] == "tomorrow"
    assert candidates[1]["horizon"] == ""
    assert candidates[1]["confidence"] == 1.0
    # History and existing predictions both reach the model.
    assert "2026-05-10" in captured[0][1]
    assert "PR the local climb" in captured[0][1]


@pytest.mark.asyncio
async def test_generate_athlete_predictions_requires_expected_outcome():
    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        return json.dumps({
            "candidates": [
                {"prediction": "Something vague", "confidence": 0.5},
                {
                    "prediction": "Will hit interval targets",
                    "expectedOutcome": "Completes all reps at target power",
                    "confidence": 0.6,
                },
            ]
        })

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        candidates = await ai_service.generate_athlete_predictions(
            "history", provider="openai"
        )

    # The candidate without an expected outcome cannot be checked and is dropped.
    assert [c["prediction"] for c in candidates] == ["Will hit interval targets"]


@pytest.mark.asyncio
async def test_generate_athlete_predictions_empty_history_skips_model():
    called = False

    async def fake_chat(*args, **kwargs):
        nonlocal called
        called = True
        return "{}"

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        candidates = await ai_service.generate_athlete_predictions(
            "   ", provider="openai"
        )

    assert candidates == []
    assert called is False


@pytest.mark.asyncio
async def test_generate_athlete_predictions_dedupes_and_caps():
    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        candidates = [
            {"prediction": "Recovers by tomorrow", "expectedOutcome": "HR baseline", "confidence": 0.6},
            {"prediction": " recovers BY tomorrow ", "expectedOutcome": "HR baseline", "confidence": 0.7},
        ]
        candidates += [
            {"prediction": f"Prediction {i}", "expectedOutcome": "outcome", "confidence": 0.5}
            for i in range(ai_service.MAX_GENERATED_PREDICTIONS + 5)
        ]
        return json.dumps({"candidates": candidates})

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        candidates = await ai_service.generate_athlete_predictions(
            "history", provider="openai"
        )

    predictions = [c["prediction"] for c in candidates]
    assert predictions.count("Recovers by tomorrow") == 1
    assert len(candidates) == ai_service.MAX_GENERATED_PREDICTIONS


@pytest.mark.asyncio
async def test_generate_athlete_predictions_handles_malformed_payload():
    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        return json.dumps({"unexpected": "shape"})

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        candidates = await ai_service.generate_athlete_predictions(
            "history", provider="openai"
        )

    assert candidates == []


# ---------------------------------------------------------------------------
# evaluate_athlete_predictions — score pending predictions against the data
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_athlete_predictions_returns_scored_verdicts():
    captured: list[tuple[str, str]] = []

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        captured.append((system_prompt, user_msg))
        return json.dumps({
            "evaluations": [
                {"index": 0, "verdict": "correct", "actualOutcome": "HR back to baseline on 05-11"},
                {"index": 1, "verdict": "incorrect", "actualOutcome": "Still fatigued, missed targets"},
                # 'unknown' verdicts are dropped so the prediction stays pending.
                {"index": 2, "verdict": "unknown", "actualOutcome": "Too early to tell"},
            ]
        })

    predictions = [
        {"prediction": "Recovered tomorrow", "expected_outcome": "HR baseline", "horizon": "tomorrow"},
        {"prediction": "Hits VO2 targets", "expected_outcome": "All reps at power", "horizon": "next ride"},
        {"prediction": "Easier week soon", "expected_outcome": "TSS drop", "horizon": "2 weeks"},
    ]

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        evaluations = await ai_service.evaluate_athlete_predictions(
            "Recent activity history:\n  2026-05-11 | ...",
            predictions,
            provider="openai",
        )

    assert evaluations == [
        {"index": 0, "correct": True, "actual_outcome": "HR back to baseline on 05-11"},
        {"index": 1, "correct": False, "actual_outcome": "Still fatigued, missed targets"},
    ]
    # The predictions and their expected outcomes reach the model.
    assert "Recovered tomorrow" in captured[0][1]
    assert "HR baseline" in captured[0][1]


@pytest.mark.asyncio
async def test_evaluate_athlete_predictions_no_predictions_skips_model():
    called = False

    async def fake_chat(*args, **kwargs):
        nonlocal called
        called = True
        return "{}"

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        evaluations = await ai_service.evaluate_athlete_predictions(
            "history", [], provider="openai"
        )

    assert evaluations == []
    assert called is False


@pytest.mark.asyncio
async def test_evaluate_athlete_predictions_drops_out_of_range_and_dupes():
    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        return json.dumps({
            "evaluations": [
                {"index": 0, "verdict": "correct", "actualOutcome": "ok"},
                {"index": 0, "verdict": "incorrect", "actualOutcome": "dupe ignored"},
                {"index": 9, "verdict": "correct", "actualOutcome": "out of range"},
            ]
        })

    predictions = [
        {"prediction": "p0", "expected_outcome": "o0", "horizon": ""},
    ]

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        evaluations = await ai_service.evaluate_athlete_predictions(
            "history", predictions, provider="openai"
        )

    assert evaluations == [{"index": 0, "correct": True, "actual_outcome": "ok"}]


@pytest.mark.asyncio
async def test_evaluate_athlete_predictions_handles_malformed_payload():
    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        return json.dumps({"unexpected": "shape"})

    predictions = [{"prediction": "p0", "expected_outcome": "o0", "horizon": ""}]

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        evaluations = await ai_service.evaluate_athlete_predictions(
            "history", predictions, provider="openai"
        )

    assert evaluations == []


# ---------------------------------------------------------------------------
# detect_athlete_fact_contradictions — flag stored facts fresh data disagrees with
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detect_contradictions_returns_valid_indexed_reasons():
    captured: list[tuple[str, str]] = []

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        captured.append((system_prompt, user_msg))
        return json.dumps({
            "contradictions": [
                {"factIndex": 0, "reason": "Held 400 W for 5x4 min — above stored 320 W FTP."},
            ]
        })

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        contradictions = await ai_service.detect_athlete_fact_contradictions(
            "Recent activity history:\n  2026-07-05 | 5x4 min @ 400 W",
            ["FTP is around 320 W", "Prefers morning rides"],
            provider="openai",
        )

    assert contradictions == [
        {"factIndex": 0, "reason": "Held 400 W for 5x4 min — above stored 320 W FTP."}
    ]
    # Both the history and the numbered facts reach the model.
    assert "400 W" in captured[0][1]
    assert "0. FTP is around 320 W" in captured[0][1]


@pytest.mark.asyncio
async def test_detect_contradictions_drops_out_of_range_and_blank():
    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        return json.dumps({
            "contradictions": [
                {"factIndex": 5, "reason": "out of range"},
                {"factIndex": 0, "reason": "  "},
                {"factIndex": 1, "reason": "Contradicted by the data."},
                {"factIndex": 1, "reason": "duplicate index, ignored"},
            ]
        })

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        contradictions = await ai_service.detect_athlete_fact_contradictions(
            "history", ["fact a", "fact b"], provider="openai"
        )

    assert contradictions == [{"factIndex": 1, "reason": "Contradicted by the data."}]


@pytest.mark.asyncio
async def test_detect_contradictions_skips_model_without_facts_or_history():
    called = False

    async def fake_chat(*args, **kwargs):
        nonlocal called
        called = True
        return "{}"

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        assert await ai_service.detect_athlete_fact_contradictions(
            "history", [], provider="openai"
        ) == []
        assert await ai_service.detect_athlete_fact_contradictions(
            "   ", ["a fact"], provider="openai"
        ) == []

    assert called is False


@pytest.mark.asyncio
async def test_detect_contradictions_handles_malformed_payload():
    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        return json.dumps({"unexpected": "shape"})

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        contradictions = await ai_service.detect_athlete_fact_contradictions(
            "history", ["a fact"], provider="openai"
        )

    assert contradictions == []


# ---------------------------------------------------------------------------
# Communication style — opener variation rules
# ---------------------------------------------------------------------------


def test_coach_voice_traits_allows_occasional_fillers_but_requires_varied_openers():
    """_COACH_VOICE_TRAITS should allow occasional fillers but prevent default/repetitive openers."""
    from services.prompts import _COACH_VOICE_TRAITS

    trait_text = _COACH_VOICE_TRAITS.lower()
    assert "great question" in trait_text
    assert "occasionally" in trait_text
    assert "do not use them as a default opener" in trait_text
    assert "avoid repetitive" in trait_text
    assert "you did a tough ride yesterday" in trait_text


def test_ask_trainer_system_response_rules_allow_occasionally_and_avoid_repetition():
    """ask_trainer_system should allow occasional fillers while prohibiting repetitive openers."""
    from services.prompts import ask_trainer_system

    prompt = ask_trainer_system(
        profile={"name": "Test"},
        today="2026-05-15",
        last_7_days=[],
        next_n_days=[],
        assessment_section="",
        memory_section="",
        workout_section="",
        plan_updates_rule="",
    )
    prompt_lower = prompt.lower()
    assert "short fillers are okay occasionally" in prompt_lower
    assert "must not appear as a default opener" in prompt_lower
    assert "do not sound templated or repetitive" in prompt_lower
    assert "you did a tough ride yesterday" in prompt_lower


def test_ask_trainer_system_includes_authoritative_date_rules():
    from services.prompts import ask_trainer_system

    prompt = ask_trainer_system(
        profile={},
        today="2026-05-26",
        last_7_days=[],
        next_n_days=[],
        assessment_section="",
        memory_section="",
        workout_section="",
        plan_updates_rule="",
        date_context=(
            "Current local date context (Europe/Berlin):\n"
            "- Today is Tuesday, May 26, 2026 (2026-05-26).\n"
            "- Yesterday was Monday, May 25, 2026 (2026-05-25).\n"
            "- Tomorrow is Wednesday, May 27, 2026 (2026-05-27)."
        ),
    )

    assert "Today is Tuesday, May 26, 2026 (2026-05-26)." in prompt
    assert "Tomorrow is Wednesday, May 27, 2026 (2026-05-27)." in prompt
    # Named rather than positional since #514 moved the rules ahead of the data:
    # "above" would now point at nothing. The authority of the section is the
    # part this guards, not where it sits.
    assert "Treat the Current local date context section as authoritative" in prompt
    assert "copy it from that date context" in prompt
    assert "copy the plan entry's weekday/dateLabel fields" in prompt


def _cache_order_prompt(**overrides) -> str:
    from services.prompts import ask_trainer_system

    kwargs = dict(
        profile={"ftp": 250},
        today="2026-05-26",
        last_7_days=[],
        next_n_days=[],
        assessment_section="",
        memory_section="",
        workout_section="",
        plan_updates_rule="",
        date_context="Current local date context (Europe/Berlin):\n- Today is Tuesday.",
    )
    kwargs.update(overrides)
    return ask_trainer_system(**kwargs)


def test_static_rules_precede_all_volatile_data_for_gemini_caching():
    """Gemini's implicit cache only matches a prefix, so the rules must come first.

    Every volatile item after the rules is fine; a single volatile item *before*
    them moves the cache boundary to zero and silently costs 10x on input (#514).
    """
    prompt = _cache_order_prompt(
        metrics_history_section="Recent activity history (newest first):\n  2026-05-25 | TSS 80",
    )

    last_static = max(
        prompt.index(marker)
        for marker in (
            "Activity feedback rules:",
            "Hard constraint rules:",
            "Outlook rules:",
            "Date awareness rules:",
            "Response quality rules",
        )
    )
    # Markers have to be strings that appear *only* in the data — several rule
    # blocks name the sections they talk about ("Upcoming plan", "Current local
    # date context"), so those match inside the static text and prove nothing.
    first_volatile = min(
        prompt.index(marker)
        for marker in (
            "Athlete data for this conversation",
            "Today's date: 2026-05-26",
            'Athlete profile: {"ftp": 250}',
            "2026-05-25 | TSS 80",
        )
    )
    assert last_static < first_volatile, (
        "volatile athlete data appears before the static rule block — "
        "this breaks the cacheable prefix"
    )


def test_the_cacheable_prefix_is_identical_across_different_athletes_and_days():
    """The whole point: two unrelated requests must share a long literal prefix."""
    import os

    monday = _cache_order_prompt(
        profile={"ftp": 250},
        today="2026-05-26",
        date_context="Current local date context: Tuesday",
        metrics_history_section="Recent activity history (newest first):\n  a",
    )
    other_athlete_other_day = _cache_order_prompt(
        profile={"ftp": 310, "name": "Someone Else"},
        today="2027-11-02",
        date_context="Current local date context: Thursday",
        metrics_history_section="Recent activity history (newest first):\n  b",
    )

    shared = len(os.path.commonprefix([monday, other_athlete_other_day]))
    # The static block is thousands of characters; anything in the hundreds would
    # mean something volatile crept back toward the front.
    assert shared > 4000, f"shared prefix collapsed to {shared} characters"
    assert monday[:shared].startswith("You are a knowledgeable cycling coach")


def test_output_contract_stays_last_where_the_model_reads_it_most_reliably():
    """Format compliance is worth more than the ~200 tokens it would add to the prefix."""
    prompt = _cache_order_prompt(plan_updates_rule="- planUpdates: an array")

    assert prompt.rstrip().endswith("- planUpdates: an array")
    assert prompt.index("ALWAYS respond with a valid JSON object") > prompt.index(
        "Athlete profile:"
    )


@pytest.mark.asyncio
async def test_ask_trainer_passes_precomputed_date_context(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        ai_service, "app_today_iso", lambda timezone_name=None: "2026-05-26"
    )
    monkeypatch.setattr(
        ai_service,
        "app_date_context",
        lambda timezone_name=None: (
            "Current local date context (Europe/Berlin):\n"
            "- Today is Tuesday, May 26, 2026 (2026-05-26).\n"
            "- Yesterday was Monday, May 25, 2026 (2026-05-25).\n"
            "- Tomorrow is Wednesday, May 27, 2026 (2026-05-27)."
        ),
    )

    async def fake_chat_history(
        provider: str,
        system_prompt: str,
        messages: list[dict[str, str]],
        json_mode: bool = False,
        task: str = "coach",
        response_schema: dict | None = None,
    ) -> str:
        captured["system_prompt"] = system_prompt
        captured["messages"] = messages
        return json.dumps({"response": "Use today's easy ride.", "sources": []})

    monkeypatch.setattr(ai_service, "_chat_history", fake_chat_history)

    await ai_service.ask_trainer(
        "What should I do today?",
        plan=[],
        profile={},
        timezone_name="Europe/Berlin",
    )

    system_prompt = str(captured["system_prompt"])
    assert "Today is Tuesday, May 26, 2026 (2026-05-26)." in system_prompt
    assert "Tomorrow is Wednesday, May 27, 2026 (2026-05-27)." in system_prompt


@pytest.mark.asyncio
async def test_ask_trainer_prompt_labels_upcoming_plan_weekdays(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        ai_service, "app_today", lambda timezone_name=None: datetime.date(2026, 6, 14)
    )
    monkeypatch.setattr(
        ai_service, "app_date_context",
        lambda timezone_name=None: (
            "Current local date context (Europe/Berlin):\n"
            "- Today is Sunday, June 14, 2026 (2026-06-14).\n"
            "- Yesterday was Saturday, June 13, 2026 (2026-06-13).\n"
            "- Tomorrow is Monday, June 15, 2026 (2026-06-15)."
        ),
    )

    async def fake_chat_history(
        provider: str,
        system_prompt: str,
        messages: list[dict[str, str]],
        json_mode: bool = False,
        task: str = "coach",
        response_schema: dict | None = None,
    ) -> str:
        captured["system_prompt"] = system_prompt
        captured["messages"] = messages
        return json.dumps({"response": "Rest today and tomorrow.", "sources": []})

    monkeypatch.setattr(ai_service, "_chat_history", fake_chat_history)

    await ai_service.ask_trainer(
        "Do I rest for the next two days?",
        plan=[
            {
                "date": "2026-06-14",
                "workoutType": "rest",
                "title": "Complete Rest Day",
                "durationMinutes": 0,
            },
            {
                "date": "2026-06-15",
                "workoutType": "rest",
                "title": "Complete Rest Day",
                "durationMinutes": 0,
            },
        ],
        profile={},
        timezone_name="Europe/Berlin",
    )

    system_prompt = str(captured["system_prompt"])
    assert '"date": "2026-06-14"' in system_prompt
    assert '"weekday": "Sunday"' in system_prompt
    assert '"dateLabel": "Sunday, June 14, 2026"' in system_prompt
    assert '"relativeDay": "today"' in system_prompt
    assert '"date": "2026-06-15"' in system_prompt
    assert '"weekday": "Monday"' in system_prompt
    assert '"dateLabel": "Monday, June 15, 2026"' in system_prompt
    assert '"relativeDay": "tomorrow"' in system_prompt
    assert "copy the plan entry's weekday/dateLabel fields" in system_prompt
    assert "check every weekday/date pair" in system_prompt


def test_login_summary_system_prompt_warns_on_unconfirmed_classification():
    from services.prompts import refresh_login_summary_system

    prompt = refresh_login_summary_system()
    assert "UNCONFIRMED" in prompt
    assert "unknown or low/medium confidence" in prompt
    # The summary used to close this instruction by asking the athlete what they
    # did — in a card with no answer field. The question moved to the activity
    # itself, where it can be answered (#580); see
    # ``test_login_summary_stops_asking.py``.
    assert "Do NOT ask the athlete what they did in this summary" in prompt


def test_login_summary_user_flags_unknown_or_low_confidence_ride():
    """The most recent ride's unknown/low-confidence classification must be
    surfaced as an explicit 'do not assert a type' instruction."""
    from services.prompts import refresh_login_summary_user

    msg = refresh_login_summary_user(
        ride_insights="some narrative",
        last_ride_feedback=None,
        notes=None,
        estimated_ftp=320,
        training_plan=None,
        latest_ride_purpose="unknown",
        latest_ride_confidence="low",
        latest_ride_reason="Insufficient stream data to classify ride reliably.",
    )
    assert "could not be reliably auto-classified" in msg
    assert "Insufficient stream data" in msg
    assert "Do NOT state or imply a specific session type" in msg

    # A high-confidence classification carries no such caveat.
    msg_high = refresh_login_summary_user(
        ride_insights="some narrative",
        last_ride_feedback=None,
        notes=None,
        estimated_ftp=320,
        training_plan=None,
        latest_ride_purpose="interval_vo2max",
        latest_ride_confidence="high",
    )
    assert "could not be reliably auto-classified" not in msg_high


def test_login_summary_user_does_not_interrogate_a_non_cycling_activity():
    """A strength session is not an unsure ride: the summary must describe it as
    what it was rather than ask it what its intervals were (#578)."""
    from services.prompts import refresh_login_summary_user

    msg = refresh_login_summary_user(
        ride_insights="some narrative",
        last_ride_feedback=None,
        notes=None,
        estimated_ftp=320,
        training_plan=None,
        latest_ride_purpose="strength",
        latest_ride_confidence="high",
        latest_ride_reason=(
            "Recorded as weighttraining — a strength session, not a power-based "
            "bike ride, so no ride classification applies."
        ),
        latest_ride_sport_type="WeightTraining",
    )
    assert "not a bike ride" in msg
    assert "never ask about intervals" in msg
    assert "could not be reliably auto-classified" not in msg

    # Even if such a row were still stored as unknown/low — the state every
    # pre-backfill row is in — the sport decides, not the stale confidence.
    stale = refresh_login_summary_user(
        ride_insights="some narrative",
        last_ride_feedback=None,
        notes=None,
        estimated_ftp=320,
        training_plan=None,
        latest_ride_purpose="unknown",
        latest_ride_confidence="low",
        latest_ride_reason="Insufficient stream data to classify ride reliably.",
        latest_ride_sport_type="WeightTraining",
    )
    assert "could not be reliably auto-classified" not in stale
    assert "not a bike ride" in stale


def test_login_summary_user_never_presents_absent_figures_as_logged():
    """The prompt used to close with "objective numbers (average power, TSS,
    duration) are fine to cite", and the coach duly reported that a session with
    NULL power and NULL TSS had "logged an average power and TSS" (#578)."""
    from services.prompts import refresh_login_summary_user

    msg = refresh_login_summary_user(
        ride_insights="some narrative",
        last_ride_feedback=None,
        notes=None,
        estimated_ftp=320,
        training_plan=None,
        latest_ride_purpose="strength",
        latest_ride_confidence="high",
        latest_ride_sport_type="WeightTraining",
        latest_ride_avg_power_w=None,
        latest_ride_tss=None,
    )
    assert "No average power and no TSS were recorded" in msg
    assert "absent is not zero" in msg
    assert "are fine to cite" not in msg

    # Figures that do exist are named, so the model has something concrete to
    # cite instead of a category of number declared safe.
    with_figures = refresh_login_summary_user(
        ride_insights="some narrative",
        last_ride_feedback=None,
        notes=None,
        estimated_ftp=320,
        training_plan=None,
        latest_ride_purpose="endurance",
        latest_ride_confidence="high",
        latest_ride_sport_type="Ride",
        latest_ride_avg_power_w=212,
        latest_ride_tss=61.4,
    )
    assert "average power 212 W" in with_figures
    assert "TSS 61" in with_figures
    assert "No average power and no TSS were recorded" not in with_figures


def test_login_summary_system_prompt_separates_sport_from_uncertainty():
    from services.prompts import refresh_login_summary_system

    prompt = refresh_login_summary_system()
    assert "Not every activity is a bike ride" in prompt
    assert "never ask about intervals, power targets or training zones" in prompt
    assert "absent is not zero" in prompt


@pytest.mark.asyncio
async def test_generate_login_summary_anchors_next_session_to_today(monkeypatch):
    """Regression: the login summary called the next session "today" even when it
    was days away, because the prompt never received an authoritative today."""
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        ai_service, "app_today", lambda timezone_name=None: datetime.date(2026, 7, 24)
    )
    monkeypatch.setattr(
        ai_service,
        "app_date_context",
        lambda timezone_name=None: (
            "Current local date context (Europe/Berlin):\n"
            "- Today is Friday, July 24, 2026 (2026-07-24).\n"
            "- Yesterday was Thursday, July 23, 2026 (2026-07-23).\n"
            "- Tomorrow is Saturday, July 25, 2026 (2026-07-25)."
        ),
    )

    async def fake_chat(
        provider: str,
        system_prompt: str,
        user_msg: str,
        json_mode: bool = False,
        task: str = "plan",
    ) -> str:
        captured["system_prompt"] = system_prompt
        captured["user_msg"] = user_msg
        return json.dumps(
            {
                "loginSummary": (
                    "Fresh legs today, nice work.\n"
                    "- Next session: your Long Aerobic Base Build is this Saturday."
                )
            }
        )

    monkeypatch.setattr(ai_service, "_chat", fake_chat)

    summary = await ai_service.generate_login_summary(
        ride_insights="Solid aerobic base building.",
        last_ride_feedback=None,
        notes=None,
        estimated_ftp=250,
        training_plan=[
            {
                "date": "2026-07-24",
                "workoutType": "rest",
                "title": "Complete Rest Day",
                "durationMinutes": 0,
            },
            {
                "date": "2026-07-25",
                "workoutType": "endurance",
                "title": "Long Aerobic Base Build",
                "durationMinutes": 180,
            },
        ],
        timezone_name="Europe/Berlin",
    )

    assert summary  # non-empty means it passed the completeness check
    user_msg = str(captured["user_msg"])
    system_prompt = str(captured["system_prompt"])
    # Authoritative "today" anchor reaches the prompt.
    assert "Today is Friday, July 24, 2026 (2026-07-24)." in user_msg
    # Plan days are annotated: today is a rest day; the next real session is tomorrow.
    assert '"relativeDay": "today"' in user_msg
    assert '"date": "2026-07-25"' in user_msg
    assert '"weekday": "Saturday"' in user_msg
    assert '"relativeDay": "tomorrow"' in user_msg
    # System prompt instructs the model not to assume the next session is today.
    assert "never assume it is today" in system_prompt


@pytest.mark.asyncio
async def test_ask_trainer_upcoming_days_start_today_not_past_history(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        ai_service, "app_today", lambda timezone_name=None: datetime.date(2026, 6, 15)
    )
    monkeypatch.setattr(
        ai_service,
        "app_date_context",
        lambda timezone_name=None: (
            "Current local date context (Europe/Berlin):\n"
            "- Today is Monday, June 15, 2026 (2026-06-15).\n"
            "- Yesterday was Sunday, June 14, 2026 (2026-06-14).\n"
            "- Tomorrow is Tuesday, June 16, 2026 (2026-06-16)."
        ),
    )

    async def fake_chat_history(
        provider: str,
        system_prompt: str,
        messages: list[dict[str, str]],
        json_mode: bool = False,
        task: str = "coach",
        response_schema: dict | None = None,
    ) -> str:
        captured["system_prompt"] = system_prompt
        captured["messages"] = messages
        return json.dumps({"response": "Today is rest; tomorrow is recovery.", "sources": []})

    monkeypatch.setattr(ai_service, "_chat_history", fake_chat_history)

    await ai_service.ask_trainer(
        "so upcoming days are pure resting, right?",
        plan=[
            {
                "date": "2026-06-14",
                "workoutType": "rest",
                "title": "Complete Rest Day",
                "durationMinutes": 0,
            },
            {
                "date": "2026-06-15",
                "workoutType": "rest",
                "title": "Complete Rest Day",
                "durationMinutes": 0,
            },
            {
                "date": "2026-06-16",
                "workoutType": "recovery",
                "title": "Easy Recovery Spin",
                "durationMinutes": 60,
            },
        ],
        profile={},
        timezone_name="Europe/Berlin",
    )

    system_prompt = str(captured["system_prompt"])
    assert "Last 7 days of training (historical context, not upcoming)" in system_prompt
    assert "Upcoming plan (today and future only" in system_prompt
    assert "Interpret 'upcoming', 'next', and 'coming days' as TODAY and future dates only" in system_prompt
    assert "use exactly the first N entries from Upcoming plan" in system_prompt
    assert "Do not call recovery spins, strength sessions, or other non-rest workouts 'pure rest'" in system_prompt
    upcoming_section = system_prompt.split("Upcoming plan (today and future only", 1)[1]
    assert '"date": "2026-06-15"' in upcoming_section
    assert '"weekday": "Monday"' in upcoming_section
    assert '"date": "2026-06-16"' in upcoming_section
    assert '"weekday": "Tuesday"' in upcoming_section
    assert '"date": "2026-06-14"' not in upcoming_section


@pytest.mark.asyncio
async def test_ask_trainer_prompt_anchors_june_17_berlin_recent_and_upcoming(
    monkeypatch,
):
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        ai_service, "app_today", lambda timezone_name=None: datetime.date(2026, 6, 17)
    )
    monkeypatch.setattr(
        ai_service,
        "app_date_context",
        lambda timezone_name=None: (
            "Current local date context (Europe/Berlin):\n"
            "- Today is Wednesday, June 17, 2026 (2026-06-17).\n"
            "- Yesterday was Tuesday, June 16, 2026 (2026-06-16).\n"
            "- Tomorrow is Thursday, June 18, 2026 (2026-06-18)."
        ),
    )

    async def fake_chat_history(
        provider: str,
        system_prompt: str,
        messages: list[dict[str, str]],
        json_mode: bool = False,
        task: str = "coach",
        response_schema: dict | None = None,
    ) -> str:
        captured["system_prompt"] = system_prompt
        captured["messages"] = messages
        return json.dumps({"response": "Tomorrow is your endurance ride.", "sources": []})

    monkeypatch.setattr(ai_service, "_chat_history", fake_chat_history)

    await ai_service.ask_trainer(
        "What does tomorrow look like after yesterday's activity?",
        plan=[
            {
                "date": "2026-06-16",
                "workoutType": "strength",
                "title": "Krafttraining",
                "durationMinutes": 45,
            },
            {
                "date": "2026-06-17",
                "workoutType": "recovery",
                "title": "Easy Recovery Spin",
                "durationMinutes": 45,
            },
            {
                "date": "2026-06-18",
                "workoutType": "endurance",
                "title": "Aerobic Endurance",
                "durationMinutes": 90,
            },
        ],
        profile={},
        metrics_history_section=(
            "Recent activity history (newest first, historical context only):\n"
            "- 2026-06-16 Tuesday: Krafttraining, 45 min"
        ),
        timezone_name="Europe/Berlin",
    )

    system_prompt = str(captured["system_prompt"])
    assert "Today is Wednesday, June 17, 2026 (2026-06-17)." in system_prompt
    assert "Yesterday was Tuesday, June 16, 2026 (2026-06-16)." in system_prompt
    assert "Tomorrow is Thursday, June 18, 2026 (2026-06-18)." in system_prompt
    assert "Recent activity history (newest first, historical context only)" in system_prompt
    assert "Last 7 days of training (historical context, not upcoming)" in system_prompt
    assert "Upcoming plan (today and future only" in system_prompt
    assert "copy the plan entry's weekday/dateLabel fields" in system_prompt
    upcoming_section = system_prompt.split("Upcoming plan (today and future only", 1)[1]
    assert '"date": "2026-06-17"' in upcoming_section
    assert '"weekday": "Wednesday"' in upcoming_section
    assert '"relativeDay": "today"' in upcoming_section
    assert '"date": "2026-06-18"' in upcoming_section
    assert '"weekday": "Thursday"' in upcoming_section
    assert '"relativeDay": "tomorrow"' in upcoming_section


@pytest.mark.asyncio
async def test_today_not_duplicated_in_history_and_upcoming(monkeypatch):
    """Today's plan entry must appear only in the upcoming section, not in history.

    With ``<= today`` for the history slice, today landed in both the
    "Last 7 days" and "Upcoming plan" sections.  The model then saw today
    framed as historical context AND as an upcoming event — causing it to
    confuse which day was today vs. tomorrow.
    """
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        ai_service, "app_today", lambda timezone_name=None: datetime.date(2026, 6, 23)
    )
    monkeypatch.setattr(
        ai_service,
        "app_date_context",
        lambda timezone_name=None: (
            "Current local date context (Europe/Berlin):\n"
            "- Today is Tuesday, June 23, 2026 (2026-06-23).\n"
            "- Yesterday was Monday, June 22, 2026 (2026-06-22).\n"
            "- Tomorrow is Wednesday, June 24, 2026 (2026-06-24)."
        ),
    )

    async def fake_chat(
        provider, system_prompt, messages, json_mode=False, task="coach", response_schema=None
    ):
        captured["system_prompt"] = system_prompt
        return json.dumps({"response": "ok", "sources": []})

    monkeypatch.setattr(ai_service, "_chat_history", fake_chat)

    await ai_service.ask_trainer(
        "What's today?",
        plan=[
            {"date": "2026-06-22", "workoutType": "strength", "title": "Upper Body", "durationMinutes": 45},
            {"date": "2026-06-23", "workoutType": "rest", "title": "Rest Day", "durationMinutes": 0},
            {"date": "2026-06-24", "workoutType": "vo2max", "title": "VO2 Max Intervals", "durationMinutes": 60},
        ],
        profile={},
        timezone_name="Europe/Berlin",
    )

    prompt = str(captured["system_prompt"])
    history_section, upcoming_section = prompt.split("Upcoming plan (today and future only", 1)

    # Today must be in the upcoming section
    assert '"date": "2026-06-23"' in upcoming_section
    assert '"relativeDay": "today"' in upcoming_section

    # Today must NOT appear in the history section (would create ambiguous dual framing)
    assert '"date": "2026-06-23"' not in history_section

    # Yesterday must be in history, not upcoming
    assert '"date": "2026-06-22"' in history_section
    assert '"date": "2026-06-22"' not in upcoming_section


@pytest.mark.asyncio
async def test_ask_trainer_user_message_prefixed_with_date_stamp(monkeypatch):
    """ask_trainer() must prepend a date stamp to the user message.

    The date_context block is in the system prompt, far from the actual question
    when conversation history is long.  An earlier turn that stated a wrong weekday
    sits closer to the generation point than the system prompt.  Prepending the
    stamp to the user message keeps an authoritative date immediately adjacent to
    the question, regardless of how many history turns come before it.
    """
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        ai_service, "app_today", lambda timezone_name=None: datetime.date(2026, 6, 23)
    )
    monkeypatch.setattr(
        ai_service, "app_today_stamp",
        lambda timezone_name=None: "[Tuesday, June 23, 2026 · 2026-06-23 · Europe/Berlin]",
    )
    monkeypatch.setattr(
        ai_service, "app_date_context",
        lambda timezone_name=None: "Current local date context (Europe/Berlin):\n- Today is Tuesday, June 23, 2026 (2026-06-23).",
    )

    async def fake_chat_history(
        provider, system_prompt, messages, json_mode=False, task="coach", response_schema=None
    ):
        captured["messages"] = messages
        return json.dumps({"response": "ok", "sources": []})

    monkeypatch.setattr(ai_service, "_chat_history", fake_chat_history)

    await ai_service.ask_trainer(
        "What should I do today?",
        plan=[],
        profile={},
        conversation_history=[
            # Simulate a prior turn that stated a wrong weekday
            {"role": "assistant", "content": "Tomorrow, Wednesday June 24, is your rest day."},
        ],
        timezone_name="Europe/Berlin",
    )

    messages = captured["messages"]
    last_user_msg = messages[-1]["content"]

    # Stamp must come first
    assert last_user_msg.startswith("[Tuesday, June 23, 2026 · 2026-06-23 · Europe/Berlin]")
    # Original question preserved after the stamp
    assert "What should I do today?" in last_user_msg
    # Prior wrong assistant turn is in history but the stamp is closer to generation
    assert any("wrong" in m.get("content", "") or "Wednesday" in m.get("content", "") for m in messages[:-1])


@pytest.mark.asyncio
async def test_race_event_feedback_plan_entries_include_relative_day(monkeypatch):
    """race_event_feedback() must pass today_date to _slim_plan_entry so that
    upcoming plan entries carry relativeDay ('today', 'tomorrow') context.

    Without today_date the _slim_plan_entry call skips the relativeDay block and
    the model has no proximity cue — it only sees ISO dates and has to infer
    "tomorrow" by computing the day-of-week itself, which is an error source.
    """
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        ai_service, "app_today", lambda timezone_name=None: datetime.date(2026, 6, 23)
    )
    monkeypatch.setattr(
        ai_service, "app_today_iso", lambda timezone_name=None: "2026-06-23"
    )

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, task="coach"):
        captured["user_msg"] = user_msg
        return json.dumps({"feedback": "Looks good."})

    monkeypatch.setattr(ai_service, "_chat", fake_chat)

    await ai_service.race_event_feedback(
        event={"date": "2026-08-10", "distanceKm": 100},
        plan=[
            {"date": "2026-06-23", "workoutType": "rest", "title": "Rest", "durationMinutes": 0},
            {"date": "2026-06-24", "workoutType": "vo2max", "title": "VO2 Max Intervals", "durationMinutes": 60},
            {"date": "2026-06-25", "workoutType": "endurance", "title": "Long Ride", "durationMinutes": 120},
        ],
        profile={},
        timezone_name="Europe/Berlin",
    )

    user_msg = str(captured["user_msg"])
    # Today's entry must carry relativeDay so the model knows it's today
    assert '"relativeDay": "today"' in user_msg
    # Tomorrow's entry must carry relativeDay
    assert '"relativeDay": "tomorrow"' in user_msg
    # Weekday labels must be present
    assert '"weekday": "Monday"' in user_msg or '"weekday": "Tuesday"' in user_msg


@pytest.mark.asyncio
async def test_adapt_training_plan_incomplete_days_include_weekday_labels(monkeypatch):
    """adapt_training_plan() must enrich incomplete_days with weekday/dateLabel/relativeDay
    before sending to the prompt.

    Without enrichment the model receives raw ISO dates and must infer weekday names
    itself — a known error source when reasoning about rescheduling.
    """
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        ai_service, "app_today", lambda timezone_name=None: datetime.date(2026, 6, 23)
    )
    monkeypatch.setattr(
        ai_service, "app_today_iso", lambda timezone_name=None: "2026-06-23"
    )

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, task="plan"):
        captured["user_msg"] = user_msg
        return json.dumps({"updatedDays": []})

    monkeypatch.setattr(ai_service, "_chat", fake_chat)

    await ai_service.adapt_training_plan(
        plan=[
            {"date": "2026-06-22", "workoutType": "strength", "title": "Upper Body", "durationMinutes": 45, "completed": True},
            {"date": "2026-06-23", "workoutType": "rest", "title": "Rest Day", "durationMinutes": 0},
            {"date": "2026-06-24", "workoutType": "vo2max", "title": "VO2 Max", "durationMinutes": 60},
        ],
        recent_feedback=[],
        profile={},
        timezone_name="Europe/Berlin",
    )

    user_msg = str(captured["user_msg"])
    # The completed day should be excluded from incomplete_days
    assert '"date": "2026-06-22"' not in user_msg or '"completed": true' not in user_msg
    # Today's incomplete entry must carry weekday and relativeDay
    assert '"weekday": "Monday"' in user_msg or '"weekday": "Tuesday"' in user_msg
    assert '"relativeDay": "today"' in user_msg
    # Tomorrow's entry must carry relativeDay
    assert '"relativeDay": "tomorrow"' in user_msg


@pytest.mark.asyncio
async def test_ask_trainer_returns_ride_label_update(monkeypatch):
    """ask_trainer() must pass ride_label_update from the LLM response to the caller.

    Previously the field was silently dropped because the return dict only included
    ride_note_update but not ride_label_update, so the router's result.pop("ride_label_update")
    always got None and label corrections were never persisted or sent to the frontend.
    """
    async def fake_chat_history(
        provider, system_prompt, messages, json_mode=False, task="coach", response_schema=None
    ):
        return json.dumps({
            "response": "I've updated your activity label for June 24 to OK.",
            "ride_label_update": {"activity_date": "2026-06-24", "label": "OK"},
        })

    monkeypatch.setattr(ai_service, "_chat_history", fake_chat_history)

    result = await ai_service.ask_trainer(
        "Please change the label for June 24 to OK.",
        plan=[],
        profile={},
    )

    assert result["ride_label_update"] == {"activity_date": "2026-06-24", "label": "OK"}


@pytest.mark.asyncio
async def test_ask_trainer_ride_label_update_is_none_when_absent(monkeypatch):
    """ride_label_update must be None (not KeyError) when the LLM omits the field."""
    async def fake_chat_history(
        provider, system_prompt, messages, json_mode=False, task="coach", response_schema=None
    ):
        return json.dumps({"response": "Great ride today!"})

    monkeypatch.setattr(ai_service, "_chat_history", fake_chat_history)

    result = await ai_service.ask_trainer("How did I do?", plan=[], profile={})

    assert result.get("ride_label_update") is None


# ---------------------------------------------------------------------------
# analyse_fit_activity (issue #335 coverage)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyse_fit_activity_cycling_estimates_hr_zones():
    with patch.object(
        ai_service,
        "_chat",
        new=AsyncMock(return_value='{"riderType": "allrounder", "notes": "Balanced"}'),
    ):
        result = await ai_service.analyse_fit_activity(
            sport_type="cycling",
            duration_minutes=90,
            avg_power=210,
            avg_hr=150,
            max_heart_rate=190,
        )
    assert result["estimatedFTP"] is None  # FTP never estimated
    assert "hrZones" in result
    assert result["riderType"] == "allrounder"


@pytest.mark.asyncio
async def test_analyse_fit_activity_running_has_no_hr_zones_without_max_hr():
    with patch.object(
        ai_service,
        "_chat",
        new=AsyncMock(return_value='{"riderType": "endurance"}'),
    ):
        result = await ai_service.analyse_fit_activity(
            sport_type="running",
            duration_minutes=40,
            avg_power=None,
            avg_hr=140,
            max_heart_rate=None,
        )
    assert result["estimatedFTP"] is None
    assert "hrZones" not in result
