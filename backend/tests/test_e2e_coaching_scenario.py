"""End-to-end verification scenario for the AI Coach (Task 11).

This module tests the complete coaching loop described in ``agent/improvements.md``
Task 11.  The scenario covers:

    1.  Athlete has a current plan with tomorrow's endurance ride.
    2.  Athlete does not use the coach for several days.
    3.  Three new rides sync from Strava at once:
            • one normal endurance ride (~90 min at 65 % FTP)
            • one over-paced tempo-like ride (~60 min at 80 % FTP)
            • one 15-minute easy ride (~50 % FTP)
    4.  The short ride is classified as ``short_easy_spin`` with low confidence.
    5.  Coach reviews all three newly added rides together.
    6.  Coach asks about the short ride (recovery / commute / aborted).
    7.  Athlete says: "I cut it short, legs felt heavy."
    8.  App stores the subjective note.
    9.  Coach recommends reducing or replacing the next ride.
    10. Plan update is applied only if warranted.
    11. Athlete asks for outlook.
    12. Coach explains the next 3–5 sessions naturally.

Structure
---------
* ``TestStep4RideClassification`` – pure-logic tests (no DB / HTTP) verifying that
  the three synthetic rides get the expected ride-purpose classifications.
* ``TestStep4RideBatchMetrics`` – verifies ``build_ride_metrics_chain`` produces
  the correct per-ride ``ride_purpose`` and ``classification_confidence`` values
  when given synthetic Strava streams.
* ``test_coaching_loop_end_to_end`` – single async router integration test that
  exercises steps 1, 3 (import), 5 (review), 7–8 (feedback), 9–10
  (recommendation + plan update), and 11–12 (outlook).

Manual QA Notes
---------------
To run the *live* scenario against a real AI provider:

    1. Set ``OPENAI_API_KEY`` or ``GEMINI_API_KEY``.
    2. Start the backend: ``uv run uvicorn main:app --reload``
    3. Register a user and set FTP to 250 via ``PUT /api/v1/users/me``.
    4. PUT a training plan with tomorrow's endurance ride.
    5. POST to ``/api/v1/ai/analyse-activities`` with the three rides below
       (add realistic Strava stream data in ``streams_by_id`` if available).
    6. POST ``/api/v1/ai/review-new-rides`` — expect all three rides mentioned and
       a follow-up question about the short ride.
    7. PATCH ``/api/v1/users/me/ride-feedback/<short_ride_id>`` with intent=aborted
       and note "legs felt heavy".
    8. POST ``/api/v1/ai/next-ride-recommendation`` — expect a recovery or easier
       recommendation with a plan update for tomorrow.
    9. Verify GET ``/api/v1/users/me/plan`` shows the updated entry.
    10. POST ``/api/v1/ai/ask-trainer`` with question "What does my next week look
        like?" — expect 3–5 sessions described.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import AsyncMock

import pytest

import services.ai_service as ai_service
from services.analysis import (
    build_ride_metrics_chain,
    classify_ride_confidence_and_reason,
    classify_ride_purpose,
)

# ---------------------------------------------------------------------------
# Scenario constants
# ---------------------------------------------------------------------------

FTP = 250  # watts

# Strava activity IDs used in the scenario
ENDURANCE_RIDE_ID = 30001
TEMPO_RIDE_ID = 30002
SHORT_EASY_RIDE_ID = 30003

PROFILE = {
    "name": "Scenario Rider",
    "email": "scenario@example.com",
    "bikeType": "road",
    "trainingGoal": "ftp_improvement",
    "weeklyHours": 10,
    "followsTrainingPlan": True,
    "fitnessLevel": "intermediate",
}


# ---------------------------------------------------------------------------
# Helper: synthetic Strava streams
# ---------------------------------------------------------------------------


def _constant_watts_stream(watts: float, duration_seconds: int) -> dict:
    """Return a minimal Strava-style stream dict with constant wattage."""
    n = duration_seconds  # 1 sample per second
    return {
        "watts": {"data": [watts] * n},
        "time": {"data": list(range(n))},
    }


# ---------------------------------------------------------------------------
# Step 4 – Classification unit tests (no DB/HTTP)
# ---------------------------------------------------------------------------


class TestStep4RideClassification:
    """Verify each of the three synthetic rides gets the expected classification.

    These tests use ``classify_ride_purpose()`` directly — no DB, no HTTP.
    """

    def test_short_easy_ride_classified_as_short_easy_spin(self):
        """15-min easy ride at 50 % FTP → short_easy_spin."""
        streams = _constant_watts_stream(FTP * 0.50, 15 * 60)
        category = classify_ride_purpose(
            watts=streams["watts"]["data"],
            time_stream=streams["time"]["data"],
            ftp=FTP,
        )
        assert category == "short_easy_spin"

    def test_short_easy_spin_confidence_is_low(self):
        """short_easy_spin always returns 'low' confidence."""
        confidence, reason = classify_ride_confidence_and_reason(
            "short_easy_spin", 15 * 60, []
        )
        assert confidence == "low"
        assert len(reason) > 0

    def test_normal_endurance_ride_classified_correctly(self):
        """90-min ride at 65 % FTP → endurance."""
        streams = _constant_watts_stream(FTP * 0.65, 90 * 60)
        category = classify_ride_purpose(
            watts=streams["watts"]["data"],
            time_stream=streams["time"]["data"],
            ftp=FTP,
        )
        assert category == "endurance"

    def test_over_paced_ride_classified_as_tempo(self):
        """60-min ride at 80 % FTP → tempo (over-paced for an endurance day)."""
        streams = _constant_watts_stream(FTP * 0.80, 60 * 60)
        category = classify_ride_purpose(
            watts=streams["watts"]["data"],
            time_stream=streams["time"]["data"],
            ftp=FTP,
        )
        assert category == "tempo"


# ---------------------------------------------------------------------------
# Step 4 – build_ride_metrics_chain integration
# ---------------------------------------------------------------------------


class TestStep4RideBatchMetrics:
    """Verify that ``build_ride_metrics_chain`` returns correct ride_purpose and
    classification_confidence for all three scenario rides when given synthetic
    Strava streams."""

    def test_metrics_chain_produces_correct_purposes(self):
        """All three rides get the right classification from build_ride_metrics_chain."""
        today = date.today()
        three_days_ago = (today - timedelta(days=3)).isoformat()
        two_days_ago = (today - timedelta(days=2)).isoformat()
        yesterday = (today - timedelta(days=1)).isoformat()

        rides = [
            {
                "strava_activity_id": ENDURANCE_RIDE_ID,
                "activity_date": three_days_ago,
                "sport_type": "cycling",
                "duration_seconds": 90 * 60,
                "streams": _constant_watts_stream(FTP * 0.65, 90 * 60),
            },
            {
                "strava_activity_id": TEMPO_RIDE_ID,
                "activity_date": two_days_ago,
                "sport_type": "cycling",
                "duration_seconds": 60 * 60,
                "streams": _constant_watts_stream(FTP * 0.80, 60 * 60),
            },
            {
                "strava_activity_id": SHORT_EASY_RIDE_ID,
                "activity_date": yesterday,
                "sport_type": "cycling",
                "duration_seconds": 15 * 60,
                "streams": _constant_watts_stream(FTP * 0.50, 15 * 60),
            },
        ]

        metrics = build_ride_metrics_chain(rides, ftp=FTP)

        assert len(metrics) == 3

        # Metrics are returned sorted by activity_date ascending
        by_id = {m["strava_activity_id"]: m for m in metrics}

        endurance_m = by_id[ENDURANCE_RIDE_ID]
        assert endurance_m["ride_purpose"] == "endurance"
        assert endurance_m["classification_confidence"] in ("high", "medium")

        tempo_m = by_id[TEMPO_RIDE_ID]
        assert tempo_m["ride_purpose"] == "tempo"
        assert tempo_m["classification_confidence"] == "medium"

        short_m = by_id[SHORT_EASY_RIDE_ID]
        assert short_m["ride_purpose"] == "short_easy_spin"
        assert short_m["classification_confidence"] == "low"
        assert len(short_m["classification_reason"]) > 0

    def test_short_ride_tss_is_minimal(self):
        """Short easy ride should produce a much lower TSS than the endurance ride."""
        today = date.today()
        two_days_ago = (today - timedelta(days=2)).isoformat()
        yesterday = (today - timedelta(days=1)).isoformat()

        rides = [
            {
                "strava_activity_id": ENDURANCE_RIDE_ID,
                "activity_date": two_days_ago,
                "sport_type": "cycling",
                "duration_seconds": 90 * 60,
                "streams": _constant_watts_stream(FTP * 0.65, 90 * 60),
            },
            {
                "strava_activity_id": SHORT_EASY_RIDE_ID,
                "activity_date": yesterday,
                "sport_type": "cycling",
                "duration_seconds": 15 * 60,
                "streams": _constant_watts_stream(FTP * 0.50, 15 * 60),
            },
        ]

        metrics = build_ride_metrics_chain(rides, ftp=FTP)
        by_id = {m["strava_activity_id"]: m for m in metrics}

        endurance_tss = by_id[ENDURANCE_RIDE_ID]["tss"] or 0.0
        short_tss = by_id[SHORT_EASY_RIDE_ID]["tss"] or 0.0

        assert short_tss < endurance_tss, (
            f"Short ride TSS ({short_tss:.1f}) should be less than endurance TSS ({endurance_tss:.1f})"
        )


# ---------------------------------------------------------------------------
# Steps 1, 3, 5, 7–12 – Full API integration scenario
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coaching_loop_end_to_end(client, auth_headers, monkeypatch):
    """Exercise the complete coaching loop (steps 1–12) via the HTTP API.

    AI service functions are mocked so the test is deterministic and does not
    require a real LLM key.  The test asserts on HTTP status codes and on the
    data that the backend stores / returns.
    """

    today = date.today()
    tomorrow = (today + timedelta(days=1)).isoformat()
    yesterday = (today - timedelta(days=1)).isoformat()
    two_days_ago = (today - timedelta(days=2)).isoformat()
    three_days_ago = (today - timedelta(days=3)).isoformat()

    # ------------------------------------------------------------------
    # Step 1: Set up the athlete profile with a known FTP and a training
    # plan containing tomorrow's endurance ride.
    # ------------------------------------------------------------------

    await client.put(
        "/api/v1/users/me",
        headers=auth_headers,
        json={
            "bikeType": "road",
            "trainingGoal": "ftp_improvement",
            "weeklyHours": 10,
            "fitnessLevel": "intermediate",
            "followsTrainingPlan": True,
            "currentFtp": FTP,
        },
    )

    plan_response = await client.put(
        "/api/v1/users/me/plan",
        headers=auth_headers,
        json={
            "plan": [
                {
                    "date": tomorrow,
                    "workoutType": "endurance",
                    "title": "Endurance Ride",
                    "description": "Steady aerobic ride at 65-75% FTP",
                    "durationMinutes": 90,
                    "completed": False,
                }
            ]
        },
    )
    assert plan_response.status_code == 200

    # ------------------------------------------------------------------
    # Step 2: Athlete does not use the coach for several days.
    # (Nothing to do here — the test just skips ahead to the batch import.)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Step 3: Three new rides sync from Strava at once.
    #
    # Because the test environment has no real Strava token, the backend
    # falls back to summary-only analysis (ride_purpose = "unknown").
    # Step 4 classification is verified separately via the unit tests above;
    # the integration test focuses on the coaching flow.
    # ------------------------------------------------------------------

    monkeypatch.setattr(
        ai_service,
        "analyse_strava_activities",
        AsyncMock(
            return_value={
                "estimatedFTP": FTP,
                "riderType": "allrounder",
                "notes": "Balanced rider with recent fatigue.",
                "rideInsights": "Three rides imported: endurance, tempo, and short easy spin.",
                "lastRideFeedback": "Short easy spin detected — was this recovery or an aborted workout?",
                "loginSummary": None,
            }
        ),
    )

    import_response = await client.post(
        "/api/v1/ai/analyse-activities",
        headers=auth_headers,
        json={
            "activities": [
                {
                    "id": ENDURANCE_RIDE_ID,
                    "name": "Morning Endurance Ride",
                    "type": "Ride",
                    "distance": 60000,
                    "movingTime": 90 * 60,
                    "elapsedTime": 91 * 60,
                    "totalElevationGain": 450,
                    "startDate": f"{three_days_ago}T07:00:00Z",
                    "averageWatts": round(FTP * 0.65),
                },
                {
                    "id": TEMPO_RIDE_ID,
                    "name": "Tempo Effort (over-paced)",
                    "type": "Ride",
                    "distance": 45000,
                    "movingTime": 60 * 60,
                    "elapsedTime": 61 * 60,
                    "totalElevationGain": 200,
                    "startDate": f"{two_days_ago}T07:00:00Z",
                    "averageWatts": round(FTP * 0.80),
                },
                {
                    "id": SHORT_EASY_RIDE_ID,
                    "name": "Short Easy Spin",
                    "type": "Ride",
                    "distance": 8000,
                    "movingTime": 15 * 60,
                    "elapsedTime": 15 * 60,
                    "totalElevationGain": 30,
                    "startDate": f"{yesterday}T08:00:00Z",
                    "averageWatts": round(FTP * 0.50),
                },
            ]
        },
    )
    assert import_response.status_code == 200
    # Three ride metrics should now exist in the database
    assert import_response.json()["assessment"]["riderType"] == "allrounder"

    # ------------------------------------------------------------------
    # Step 5: Coach reviews all three newly added rides together.
    #
    # The mock returns a review that mentions all three ride types and
    # contains a follow-up question about the short ride (step 6).
    # ------------------------------------------------------------------

    batch_review_text = (
        "You have three new rides to review. "
        "The 90-min endurance ride (3 days ago) was solid base work. "
        "The 60-min tempo effort (2 days ago) was over-paced for an endurance day — "
        "your average power was 80 % FTP instead of the target 65–75 %. "
        "The 15-min easy spin (yesterday) is too short to assess as aerobic training. "
        "Was that a recovery ride, a commute, or did you cut it short?"
    )

    monkeypatch.setattr(
        ai_service,
        "batch_review_rides",
        AsyncMock(return_value=batch_review_text),
    )

    review_response = await client.post(
        "/api/v1/ai/review-new-rides",
        headers=auth_headers,
    )
    assert review_response.status_code == 200
    review_body = review_response.json()

    # Step 5: All three rides were included in the review
    assert review_body["rideCount"] == 3

    # Step 5: Review mentions different ride types
    review_text = review_body["review"]
    assert "endurance" in review_text.lower()
    assert "tempo" in review_text.lower() or "over-paced" in review_text.lower()

    # Step 6: Review contains a question about the short ride
    assert "?" in review_text

    # After the review, rides should be marked as reviewed
    # (a second call returns rideCount=0)
    second_review = await client.post(
        "/api/v1/ai/review-new-rides",
        headers=auth_headers,
    )
    assert second_review.status_code == 200
    assert second_review.json()["rideCount"] == 0

    # ------------------------------------------------------------------
    # Steps 7 & 8: Athlete says "I cut it short, legs felt heavy."
    # The app stores the subjective note via the ride-feedback endpoint.
    # ------------------------------------------------------------------

    feedback_response = await client.patch(
        f"/api/v1/users/me/ride-feedback/{SHORT_EASY_RIDE_ID}",
        headers=auth_headers,
        json={
            "rpe": 3,
            "legs": "heavy",
            "intent": "aborted",
            "note": "I cut it short, legs felt heavy.",
        },
    )
    assert feedback_response.status_code == 200
    feedback_body = feedback_response.json()
    assert feedback_body["stravaActivityId"] == SHORT_EASY_RIDE_ID

    # The stored note should contain the athlete's explanation.
    # The backend formats the note as "RPE 3/10 | legs: heavy | intent: aborted | <note>",
    # so "heavy" comes from the legs field and "aborted" from the intent field.
    stored_note = feedback_body["userNote"]
    assert "heavy" in stored_note
    assert "aborted" in stored_note

    # ------------------------------------------------------------------
    # Steps 9 & 10: Coach recommends reducing tomorrow's ride.
    # The mock returns a 'recovery' recommendation with a plan update.
    # ------------------------------------------------------------------

    recovery_recommendation = {
        "response": (
            "Given your heavy legs and the over-paced tempo effort two days ago, "
            "I recommend swapping tomorrow's endurance ride for a 45-min easy recovery spin."
        ),
        "next_session_recommendation": (
            "Replace tomorrow's 90-min endurance ride with a 45-min recovery spin at 55–65 % FTP."
        ),
        "recommendation_type": "recovery",
        "plan_updates": [
            {
                "date": tomorrow,
                "workoutType": "recovery",
                "title": "Recovery Spin",
                "description": "Easy aerobic spin at 55–65 % FTP",
                "durationMinutes": 45,
            }
        ],
    }

    monkeypatch.setattr(
        ai_service,
        "recommend_next_session",
        AsyncMock(return_value=recovery_recommendation),
    )

    rec_response = await client.post(
        "/api/v1/ai/next-ride-recommendation",
        headers=auth_headers,
        json={},
    )
    assert rec_response.status_code == 200
    rec_body = rec_response.json()

    # Step 9: Coach recommends recovery
    assert rec_body["recommendationType"] == "recovery"
    assert "heavy" in rec_body["response"].lower() or "recovery" in rec_body["response"].lower()

    # Step 10: Plan update is present and has been applied
    assert rec_body["planUpdates"] is not None
    assert len(rec_body["planUpdates"]) == 1

    # Verify the plan was actually updated in the database
    plan_after = await client.get("/api/v1/users/me/plan", headers=auth_headers)
    assert plan_after.status_code == 200
    updated_day = next(
        (d for d in plan_after.json()["plan"] if d["date"] == tomorrow),
        None,
    )
    assert updated_day is not None, "Tomorrow's plan day must exist after recommendation"
    assert updated_day["workoutType"] == "recovery"
    assert updated_day["durationMinutes"] == 45

    # ------------------------------------------------------------------
    # Steps 11 & 12: Athlete asks for outlook on the next few sessions.
    # ------------------------------------------------------------------

    outlook_response_text = (
        "Here is your outlook for the next 4 sessions: "
        "1. Tomorrow (recovery spin, 45 min) — let your legs recover. "
        "2. Wednesday (endurance, 75 min) — back to base building if legs feel fresher. "
        "3. Thursday (rest or easy spin) — optional active recovery. "
        "4. Saturday (endurance with tempo blocks, 90 min) — build tempo work back in. "
        "Focus on keeping the endurance days truly aerobic this week."
    )

    monkeypatch.setattr(
        ai_service,
        "ask_trainer",
        AsyncMock(
            return_value={
                "response": outlook_response_text,
                "plan_updates": None,
                "sources": [],
            }
        ),
    )

    monkeypatch.setattr(
        ai_service,
        "classify_question",
        AsyncMock(return_value={"category": "plan_query", "needs_science_rag": False}),
    )

    outlook_ask = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "What does my next week look like?"},
    )
    assert outlook_ask.status_code == 200
    outlook_body = outlook_ask.json()

    # Step 12: Response covers upcoming sessions and mentions recovery
    assert len(outlook_body["response"]) > 50  # non-trivial answer
    response_lower = outlook_body["response"].lower()
    # Outlook must reference multiple future sessions
    assert "session" in response_lower or "ride" in response_lower or "tomorrow" in response_lower
    # Outlook reflects the current fatigue context (recovery theme)
    assert "recover" in response_lower or "easy" in response_lower or "rest" in response_lower
