"""Reviewing a ride: rate_completed_workout, the batch review, and the ride section of the prompt.

Split out of ``test_ai_service_unit.py`` (ai-trainer-ops#259); its history is there.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

import services.ai_service as ai_service


@pytest.mark.asyncio
async def test_rate_completed_workout_no_feedback():
    """rate_completed_workout returns empty feedback dict when feedback is missing."""
    result = await ai_service.rate_completed_workout(
        day={
            "workoutType": "endurance",
            "title": "Ride",
            "description": "...",
            "durationMinutes": 60,
        },
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
        return json.dumps(
            {
                "feedback": "Great effort today! You nailed the power targets.",
                "flag_for_adaptation": False,
            }
        )

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        result = await ai_service.rate_completed_workout(
            day, profile, provider="openai"
        )

    assert isinstance(result, dict)
    assert (
        "effort" in result["feedback"].lower() or "power" in result["feedback"].lower()
    )
    assert result["flag_for_adaptation"] is False


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
            {
                "start_min": 0.0,
                "end_min": 15.0,
                "avg_power_w": 240,
                "pct_over_target": 24.4,
            }
        ],
    }

    captured_user_msg: list[str] = []

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        captured_user_msg.append(user_msg)
        return json.dumps(
            {
                "feedback": "You went over intensity — ease back next time.",
                "flag_for_adaptation": False,
            }
        )

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
        return json.dumps(
            {"feedback": "Great session, well done!", "flag_for_adaptation": False}
        )

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        result = await ai_service.rate_completed_workout(day, {}, provider="openai")

    assert result["flag_for_adaptation"] is False


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
        classification_reason = (
            "Sustained aerobic effort across adequate ride duration."
        )
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
        classification_reason = (
            "Average power in tempo band with no distinct interval blocks detected."
        )
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
    assert "Planned workout: «Planned Tempo», 80 min" in section


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
    assert 'badge:"Close" (set explicitly' in section
    # The planned title is athlete-editable, so it arrives marked (#680).
    title = FakeMetric.matched_plan_snapshot["title"]
    assert f"Planned workout: «{title}», 180 min" in section


def test_batch_review_user_includes_ambiguous_plan_match():
    from services.prompts import batch_review_user, plan_linkage_phrase

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
    assert f"Plan linkage: {plan_linkage_phrase('ambiguous')}" in msg
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
    assert (
        result["follow_up_question"]
        == "Was this intentional recovery, a commute, or did you cut it short?"
    )
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
        "feedback": {
            "actualDurationMinutes": 90,
            "perceivedEffort": 2,
            "notes": "Felt solid",
        },
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
    assert (
        "at most one" in prompt or "one follow-up" in prompt or "one concise" in prompt
    )
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
    assert "over-paced endurance" in prompt or (
        "z3/z4" in prompt and "endurance" in prompt
    )


def test_rate_workout_system_includes_example_missed_aborted_workout():
    """rate_workout_system must contain an example for a missed or aborted workout."""
    from services.prompts import rate_workout_system

    prompt = rate_workout_system().lower()
    assert "missed" in prompt or "aborted" in prompt


def test_rate_workout_system_includes_example_successful_interval_day():
    """rate_workout_system must contain an example for a successful interval day."""
    from services.prompts import rate_workout_system

    prompt = rate_workout_system().lower()
    assert "successful interval" in prompt or (
        "threshold" in prompt and "target" in prompt and "solid" in prompt
    )
