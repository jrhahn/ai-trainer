"""POST /ai/review-new-rides, the ride history, and the follow-up questions after a ride.

Split out of ``test_ai.py`` (ai-trainer-ops#259); its history is there.
"""

from unittest.mock import patch

import pytest

import services.ai_service as ai_service


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


@pytest.mark.asyncio
async def test_ride_metrics_history_backfills_missing_planned_workout(
    client, auth_headers
):
    import crud
    from auth import decode_token
    from tests.conftest import TestSessionLocal

    token = auth_headers["Authorization"].split(" ", 1)[1]
    user_id = decode_token(token)
    async with TestSessionLocal() as db:
        await crud.upsert_training_plan(
            db,
            user_id,
            [
                {
                    "date": "2026-05-07",
                    "workoutType": "endurance",
                    "title": "Easy Recovery Spin",
                    "description": "Keep it light",
                    "durationMinutes": 60,
                }
            ],
        )
        await crud.upsert_ride_metric(
            db,
            user_id,
            strava_activity_id=61002,
            activity_date="2026-05-07",
            sport_type="Ride",
            activity_name="Afternoon Ride",
            duration_seconds=3300,
        )
        await db.commit()

    history = await client.get("/api/v1/users/me/ride-metrics-history", headers=auth_headers)
    ride = next(r for r in history.json()["rides"] if r["stravaActivityId"] == 61002)
    assert ride["planMatchStatus"] == "auto_matched"
    assert ride["matchedPlanDate"] == "2026-05-07"
    assert ride["matchedPlanSnapshot"]["title"] == "Easy Recovery Spin"


@pytest.mark.asyncio
async def test_ride_metrics_history_clears_processing_day_plan_mismatch(
    client, auth_headers
):
    import crud
    from auth import decode_token
    from tests.conftest import TestSessionLocal

    token = auth_headers["Authorization"].split(" ", 1)[1]
    user_id = decode_token(token)
    async with TestSessionLocal() as db:
        await crud.upsert_training_plan(
            db,
            user_id,
            [
                {
                    "date": "2026-05-28",
                    "workoutType": "rest",
                    "title": "Complete Rest Day",
                    "description": "No training planned",
                    "durationMinutes": 0,
                }
            ],
        )
        ride = await crud.upsert_ride_metric(
            db,
            user_id,
            strava_activity_id=61003,
            activity_date="2026-05-27",
            sport_type="Ride",
            activity_name="Afternoon Ride",
            duration_seconds=7680,
        )
        await crud.update_ride_match(
            db,
            ride,
            status="auto_matched",
            matched_plan_date="2026-05-28",
            matched_plan_snapshot={
                "date": "2026-05-28",
                "workoutType": "rest",
                "title": "Complete Rest Day",
                "durationMinutes": 0,
            },
        )
        await db.commit()

    history = await client.get("/api/v1/users/me/ride-metrics-history", headers=auth_headers)
    ride = next(r for r in history.json()["rides"] if r["stravaActivityId"] == 61003)
    assert ride["activityDate"] == "2026-05-27"
    assert ride["planMatchStatus"] == "unmatched"
    assert ride["matchedPlanDate"] is None
    assert ride["matchedPlanSnapshot"] is None


@pytest.mark.asyncio
async def test_ride_metrics_history_uses_activity_date_for_rest_day_context(
    client, auth_headers
):
    import crud
    from auth import decode_token
    from tests.conftest import TestSessionLocal

    token = auth_headers["Authorization"].split(" ", 1)[1]
    user_id = decode_token(token)
    async with TestSessionLocal() as db:
        await crud.upsert_training_plan(
            db,
            user_id,
            [
                {
                    "date": "2026-05-27",
                    "workoutType": "rest",
                    "title": "Complete Rest Day",
                    "description": "No training planned",
                    "durationMinutes": 0,
                },
                {
                    "date": "2026-05-28",
                    "workoutType": "recovery",
                    "title": "Active Recovery Spin",
                    "description": "Easy spin",
                    "durationMinutes": 45,
                },
            ],
        )
        await crud.upsert_ride_metric(
            db,
            user_id,
            strava_activity_id=61004,
            activity_date="2026-05-27",
            sport_type="Ride",
            activity_name="Afternoon Ride",
            duration_seconds=7680,
        )
        await db.commit()

    history = await client.get("/api/v1/users/me/ride-metrics-history", headers=auth_headers)
    ride = next(r for r in history.json()["rides"] if r["stravaActivityId"] == 61004)
    assert ride["planMatchStatus"] == "unmatched"
    assert ride["matchedPlanDate"] == "2026-05-27"
    assert ride["matchedPlanSnapshot"]["title"] == "Complete Rest Day"


@pytest.mark.asyncio
async def test_analyse_activities_preserves_detailed_strava_sport_type(
    client, auth_headers, mock_ai_service
):
    response = await client.post(
        "/api/v1/ai/analyse-activities",
        headers=auth_headers,
        json={
            "activities": [
                {
                    "id": 62003,
                    "name": "Evening mobility",
                    "type": "Workout",
                    "sportType": "Yoga",
                    "distance": 0,
                    "movingTime": 2700,
                    "elapsedTime": 2700,
                    "totalElevationGain": 0,
                    "startDate": "2026-05-05T20:00:00Z",
                }
            ]
        },
    )
    assert response.status_code == 200

    analyse_call = mock_ai_service["analyse_strava_activities"].await_args
    assert analyse_call is not None
    assert analyse_call.args[0][0]["sport_type"] == "Yoga"

    history = await client.get("/api/v1/users/me/ride-metrics-history", headers=auth_headers)
    ride = next(r for r in history.json()["rides"] if r["stravaActivityId"] == 62003)
    assert ride["sportType"] == "Yoga"


@pytest.mark.asyncio
async def test_analyse_activities_uses_start_date_local_for_activity_date(
    client, auth_headers, mock_ai_service
):
    response = await client.post(
        "/api/v1/ai/analyse-activities",
        headers=auth_headers,
        json={
            "activities": [
                {
                    "id": 62004,
                    "name": "Late evening ride",
                    "type": "Ride",
                    "distance": 12000,
                    "movingTime": 1800,
                    "elapsedTime": 1800,
                    "totalElevationGain": 120,
                    "startDate": "2026-05-05T23:30:00Z",
                    "startDateLocal": "2026-05-06T07:30:00",
                }
            ]
        },
    )
    assert response.status_code == 200

    history = await client.get("/api/v1/users/me/ride-metrics-history", headers=auth_headers)
    ride = next(r for r in history.json()["rides"] if r["stravaActivityId"] == 62004)
    assert ride["activityDate"] == "2026-05-06"


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


@pytest.mark.asyncio
async def test_analyse_activities_persists_updates_without_clobbering_protected_days(
    client, auth_headers, mock_ai_service
):
    """analyse-activities persists ride-review plan updates through the shared
    pin/completed-respecting pipeline (source="ride_review"), instead of leaving
    them for a full-plan client PUT that reverted concurrent edits and rewrote
    protected days (stale-snapshot clobber, #399).

    A completed day must stay untouched; an open, un-pinned day may be adapted
    and is persisted server-side without any /users/me/plan PUT from the client.
    """
    import crud
    from tests.conftest import TestSessionLocal

    plan = [
        {
            "date": "2026-04-10",
            "workoutType": "intervals",
            "title": "VO2 Max Intervals",
            "description": "Hard intervals",
            "durationMinutes": 60,
            "completed": True,
        },
        {
            "date": "2026-04-12",
            "workoutType": "endurance",
            "title": "Endurance Ride",
            "description": "Steady aerobic",
            "durationMinutes": 90,
        },
    ]
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_email(db, "rider@example.com")
        await crud.upsert_training_plan(db, user.id, plan)
        await db.commit()

    # The ride-review analysis proposes downgrading BOTH days to recovery.
    mock_ai_service["analyse_strava_activities"].return_value = {
        "estimatedFTP": 250,
        "riderType": "allrounder",
        "notes": "n",
        "rideInsights": "i",
        "lastRideFeedback": "f",
        "planUpdates": [
            {"date": "2026-04-10", "workoutType": "recovery", "title": "Active Recovery"},
            {"date": "2026-04-12", "workoutType": "recovery", "title": "Active Recovery"},
        ],
    }

    # Post an activity on a date outside the plan so it does not auto-match a plan
    # day (keeps the test focused on the planUpdates persistence path).
    resp = await client.post(
        "/api/v1/ai/analyse-activities",
        headers=auth_headers,
        json={
            "activities": [
                {
                    "id": 77,
                    "name": "Ride",
                    "type": "Ride",
                    "distance": 40000,
                    "movingTime": 3600,
                    "elapsedTime": 3600,
                    "totalElevationGain": 300,
                    "startDate": "2026-05-01T08:00:00Z",
                    "averageWatts": 200,
                }
            ]
        },
    )
    assert resp.status_code == 200

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_email(db, "rider@example.com")
        saved = (await crud.get_training_plan(db, user.id)).plan
    by_date = {d["date"]: d for d in saved}
    # Completed day is historical fact — never rewritten by the automated update.
    assert by_date["2026-04-10"]["workoutType"] == "intervals"
    assert by_date["2026-04-10"]["title"] == "VO2 Max Intervals"
    # Open day was adapted and persisted server-side (no client PUT needed).
    assert by_date["2026-04-12"]["workoutType"] == "recovery"


@pytest.mark.asyncio
async def test_auto_adapt_reads_what_the_athlete_wrote(client, auth_headers, mock_ai_service):
    """Auto-adapt gets the athlete's recent messages, not only what was extracted
    from them (ai-trainer-ops#152)."""
    from datetime import datetime, timedelta, timezone

    import models
    from auth import decode_token
    from tests.conftest import TestSessionLocal

    user_id = decode_token(auth_headers["Authorization"].split(" ", 1)[1])
    async with TestSessionLocal() as db:
        db.add(
            models.ChatMessage(
                user_id=user_id,
                role="user",
                content="Morgen bin ich den ganzen Tag wandern.",
                timestamp="t",
                created_at=datetime.now(timezone.utc) - timedelta(hours=1),
            )
        )
        await db.commit()
    mock_ai_service["rate_completed_workout"].return_value = {
        "feedback": "Very hard, rest is recommended.",
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
                    "notes": "Had to stop",
                    "completedAt": "2026-04-10T10:00:00Z",
                },
            },
        },
    )

    assert response.status_code == 200
    mock_ai_service["adapt_training_plan"].assert_called()
    assert "Morgen bin ich den ganzen Tag wandern." in (
        mock_ai_service["adapt_training_plan"].call_args.kwargs["athlete_words"]
    )


@pytest.mark.asyncio
async def test_both_writers_hand_the_athletes_words_to_the_model():
    words = "\nWhat the athlete wrote in the chat: WORDS-MARKER\n"
    prompts: list[str] = []

    async def chat(provider, system_prompt, user_msg, **_kwargs):
        prompts.append(user_msg)
        if len(prompts) == 1:
            return '{"updatedDays": []}'
        return '{"response": "Ok.", "nextSessionRecommendation": "Rest", "planUpdates": []}'

    with patch.object(ai_service, "_chat", side_effect=chat):
        await ai_service.adapt_training_plan([], [], {"name": "R"}, athlete_words=words)
        await ai_service.recommend_next_session([], [], {"name": "R"}, athlete_words=words)

    assert len(prompts) == 2
    assert all("WORDS-MARKER" in prompt for prompt in prompts)
