"""Activity summaries: the pending-feedback and login summaries, analysing an activity, and reading the model's JSON.

Split out of ``test_ai_service_unit.py`` (ai-trainer-ops#259); its history is there.
"""

from __future__ import annotations

import json
import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import services.ai_service as ai_service


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
    # The name is provider-supplied, so it arrives marked as data (#680), and
    # the anchor is the later of the two rides.
    [anchor] = [line for line in user_prompt.splitlines() if line.startswith("Latest listed activity")]
    assert "Name: «Oberursel (Taunus) Mountain Biking»" in anchor
    assert "Type: MountainBikeRide" in user_prompt
    assert "Mittelberg Hiking" not in user_prompt
    assert "prefer the listed activity data" in system_prompt
    assert "Ignore older free-form assessment notes" in user_prompt
    assert "mention the latest listed activity by name" in user_prompt


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


def test_login_summary_user_names_a_ride_as_a_ride():
    """A ride got no line about its sport, so a stored text about an earlier
    strength session filled the gap: the day's ride was narrated as "your
    strength session today"."""
    from services.prompts import refresh_login_summary_user

    def build(sport_type):
        return refresh_login_summary_user(
            ride_insights="Strength session on Wednesday: core and upper body.",
            last_ride_feedback="Strength session recalculated.",
            notes=None,
            estimated_ftp=250,
            training_plan=None,
            latest_ride_purpose="unknown",
            latest_ride_confidence="low",
            latest_ride_sport_type=sport_type,
            latest_ride_avg_power_w=180,
            latest_ride_tss=85.0,
        )

    msg = build("Ride")
    assert "The most recent activity was a bike ride (sport type: Ride)" in msg
    assert "about an earlier one" in msg
    # A catch-all label says nothing about the sport, so it is not called a ride.
    assert "bike ride (sport type" not in build("Workout")


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
