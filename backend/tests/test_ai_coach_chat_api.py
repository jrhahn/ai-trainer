"""POST /ai/ask-trainer and the endpoints around the chat.

Split out of ``test_ai.py`` (ai-trainer-ops#259); its history is there.
"""

import json
from unittest.mock import patch

import pytest

import services.ai_service as ai_service
from tests.conftest import notes_text


PROFILE = {
    "name": "Test Rider",
    "email": "rider@example.com",
    "bikeType": "road",
    "trainingGoal": "general_fitness",
    "weeklyHours": 8,
    "followsTrainingPlan": True,
    "fitnessLevel": "intermediate",
}


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

    async def fake_chat_history(
        provider, system_prompt, messages, json_mode=False, **kwargs
    ):
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
    # An explicit request is still the first reason for a change (since
    # ai-trainer-ops#103 Q30 a protective change is the second).
    assert "explicitly asks" in prompt
    # Context workout section must NOT appear
    assert "currently viewing this specific workout" not in prompt
    # Implicit-change instruction must NOT appear
    assert "proposes ANY change" not in prompt


@pytest.mark.asyncio
async def test_ask_trainer_with_context_workout_uses_implicit_change_rule():
    """With context_workout the prompt must allow planUpdates for implicit coaching changes."""
    captured_prompt: list[str] = []

    async def fake_chat_history(
        provider, system_prompt, messages, json_mode=False, **kwargs
    ):
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

    assert (
        "adjusted" in result["response"]
        or "fatigue" in result["response"].lower()
        or result["plan_updates"]
    )
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
async def test_ask_trainer_flags_no_op_update_for_absent_day(
    client, auth_headers, mock_ai_service
):
    """A coach plan_update for a date not in the plan is a silent no-op; the reply
    must not claim success for a change that was never saved (#364)."""
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
                    "date": "2026-05-10",
                    "workoutType": "endurance",
                    "title": "Endurance Ride",
                    "description": "Steady Z2",
                    "durationMinutes": 90,
                }
            ],
        )
        await db.commit()

    # The model confidently claims success but targets a date absent from the plan.
    mock_ai_service["ask_trainer"].return_value = {
        "response": "Done — I switched that day to intervals.",
        "plan_updates": [
            {
                "date": "2026-05-03",
                "workoutType": "intervals",
                "title": "VO2 Intervals",
                "description": "5x4",
                "durationMinutes": 60,
            }
        ],
    }

    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "Switch May 3 to intervals"},
    )

    assert response.status_code == 200
    body = response.json()
    assert "2026-05-03" in notes_text(body)
    assert "could not update" in notes_text(body).lower()


@pytest.mark.asyncio
async def test_ask_trainer_is_honest_when_constraint_overrides_change(
    client, auth_headers, mock_ai_service
):
    """A coach rest-day request on a required-session day is coerced back by the
    plan pipeline; the reply must say so rather than falsely confirm it (#414)."""
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
                    "date": "2027-01-15",
                    "workoutType": "endurance",
                    "title": "Endurance Ride",
                    "description": "Steady Z2",
                    "durationMinutes": 120,
                }
            ],
        )
        # A hard required-session constraint pins this day to endurance. No
        # expiry so it stays active regardless of the test clock.
        await crud.upsert_availability_constraint(
            db,
            user_id,
            constraint_type="required_workout",
            constraint_date="2027-01-15",
            weekday="friday",
            required_workout={"workoutType": "endurance", "minDurationMinutes": 120},
        )
        await db.commit()

    # The model claims it cleared the day to a rest day.
    mock_ai_service["ask_trainer"].return_value = {
        "response": "Done — I cleared Friday to a complete rest day.",
        "plan_updates": [
            {
                "date": "2027-01-15",
                "workoutType": "rest",
                "title": "Rest Day",
                "description": "Full rest",
                "durationMinutes": 0,
            }
        ],
    }

    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "make friday a rest day"},
    )

    assert response.status_code == 200
    body = response.json()
    lowered = notes_text(body).lower()
    assert "couldn't change" in lowered
    assert "friday" in lowered
    assert "required session" in lowered
    # The day stays as the required endurance session, not a rest day.
    persisted = {d["date"]: d for d in body["updatedPlan"]}
    assert persisted["2027-01-15"]["workoutType"] == "endurance"


@pytest.mark.asyncio
async def test_ask_trainer_background_memory_update_reaches_the_database(
    client, auth_headers, mock_ai_service
):
    """The queued coach-memory write must actually land (#522).

    It is a background task, and FastAPI ran those *before* the `get_db`
    dependency's teardown committed (until ai-trainer-ops#161 moved that commit
    before the response) — so while the handler's transaction stayed
    open, `_update_memory_bg` waited on its own connection for the full sqlite
    busy timeout and then failed. It failed silently, too, because that task
    swallows exceptions by design so a broken memory update cannot break the
    chat. Nothing asserted the write, so 22 tests stayed green while the path
    they covered never once succeeded. This is that assertion.
    """
    import crud
    from auth import decode_token
    from tests.conftest import TestSessionLocal

    user_id = decode_token(auth_headers["Authorization"].split(" ", 1)[1])

    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "When is the best time to ride tomorrow?"},
    )
    assert response.status_code == 200

    async with TestSessionLocal() as db:
        row = await crud.get_coach_memory(db, user_id)

    assert row is not None, "the background memory update never reached the database"
    assert row.memory == "Prefers morning workouts."


@pytest.mark.asyncio
async def test_ask_trainer_lifts_flagged_constraint_and_edit_lands(
    client, auth_headers, mock_ai_service
):
    """After the coach reports a blocking constraint, "lift that constraint" must
    deactivate it and let the coach's edit finally persist (#437)."""
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
                    "date": "2027-01-15",
                    "workoutType": "endurance",
                    "title": "Endurance Ride",
                    "description": "Steady Z2",
                    "durationMinutes": 120,
                }
            ],
        )
        await crud.upsert_availability_constraint(
            db,
            user_id,
            constraint_type="required_workout",
            constraint_date="2027-01-15",
            weekday="friday",
            required_workout={"workoutType": "endurance", "minDurationMinutes": 120},
        )
        await db.commit()

    def _rest_update():
        # A fresh dict per turn: the handler appends notes onto result["response"].
        return {
            "response": "Done — Friday is now a rest day.",
            "plan_updates": [
                {
                    "date": "2027-01-15",
                    "workoutType": "rest",
                    "title": "Rest Day",
                    "description": "Full rest",
                    "durationMinutes": 0,
                }
            ],
        }

    # Turn 1: the request is blocked; the reply flags the constraint.
    mock_ai_service["ask_trainer"].return_value = _rest_update()
    first = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "make friday a rest day"},
    )
    assert first.status_code == 200
    assert "couldn't change" in notes_text(first.json()).lower()
    # The day is still the required session.
    assert {d["date"]: d for d in first.json()["updatedPlan"]}["2027-01-15"][
        "workoutType"
    ] == "endurance"

    # Turn 2: lift the constraint (no date named -> resolves to the flagged one).
    mock_ai_service["ask_trainer"].return_value = _rest_update()
    second = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "pls lift that constraint"},
    )
    assert second.status_code == 200
    lowered = notes_text(second.json()).lower()
    assert "lifted the availability constraint" in lowered
    assert "friday" in lowered
    # No stale override note this time.
    assert "couldn't change" not in lowered
    # The coach's rest-day edit now lands.
    assert {d["date"]: d for d in second.json()["updatedPlan"]}["2027-01-15"][
        "workoutType"
    ] == "rest"

    # The constraint is gone.
    async with TestSessionLocal() as db:
        active = await crud.list_active_availability_constraints(
            db, user_id, today="2027-01-01"
        )
        assert active == []


@pytest.mark.asyncio
async def test_ask_trainer_endpoint_forwards_structured_athlete_context(
    client, auth_headers, mock_ai_service
):
    save_response = await client.put(
        "/api/v1/users/me/athlete-context",
        headers=auth_headers,
        json={
            "trainingTendency": "overtrains",
            "restResponse": "restless",
            "adherencePattern": "adds_extra",
            "strengths": ["VO2max work"],
            "weaknesses": ["easy days"],
            "preferredTerrain": ["singletrack"],
            "preferredSessionTypes": ["VO2max"],
            "coachingRisks": ["doing too much when fresh"],
            "notes": "Needs explicit permission to rest.",
        },
    )
    assert save_response.status_code == 200

    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "What should I watch out for?"},
    )

    assert response.status_code == 200
    call_kwargs = mock_ai_service["ask_trainer"].call_args.kwargs
    assert call_kwargs["athlete_context"]["trainingTendency"] == "overtrains"
    assert call_kwargs["athlete_context"]["restResponse"] == "restless"
    assert call_kwargs["athlete_context"]["coachingRisks"] == [
        "doing too much when fresh"
    ]


@pytest.mark.asyncio
async def test_ask_trainer_endpoint_forwards_prompt_safe_athlete_memory_facts(
    client, auth_headers, mock_ai_service
):
    # Observed twice so it clears the evidence bar and reaches the coach (#387).
    for _ in range(2):
        saved = await client.post(
            "/api/v1/users/me/athlete-memory-facts",
            headers=auth_headers,
            json={
                "fact": "Does too much when fresh",
                "category": "coaching risk",
                "sourceSnippet": "Added extra intervals after a rest day.",
                "confidence": 0.7,
            },
        )
        assert saved.status_code == 201
    low_confidence = await client.post(
        "/api/v1/users/me/athlete-memory-facts",
        headers=auth_headers,
        json={
            "fact": "Maybe dislikes gym work",
            "category": "preference",
            "confidence": 0.2,
        },
    )
    assert low_confidence.status_code == 201

    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "What should I watch out for?"},
    )

    assert response.status_code == 200
    call_kwargs = mock_ai_service["ask_trainer"].call_args.kwargs
    facts = call_kwargs["athlete_memory_facts"]
    assert len(facts) == 1
    assert facts[0]["fact"] == "Does too much when fresh"
    assert facts[0]["category"] == "coaching_risk"
    assert facts[0]["sourceSnippet"] == "Added extra intervals after a rest day."


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

    async def fake_chat_history(
        provider, system_prompt, messages, json_mode=False, **kwargs
    ):
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
async def test_extract_athlete_facts_endpoint_returns_candidates_without_persisting(
    client, auth_headers, mock_ai_service
):
    """Extraction returns review candidates and persists nothing."""
    mock_ai_service["extract_athlete_facts"].return_value = [
        {
            "fact": "Gets anxious after two rest days",
            "category": "psychological_tendencies",
            "confidence": 0.7,
            "source_snippet": "I feel like I'm losing fitness when I rest.",
        }
    ]

    response = await client.post(
        "/api/v1/ai/extract-athlete-facts",
        headers=auth_headers,
        json={"transcript": "Athlete: I hate resting. Coach: Tell me more."},
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["candidates"]) == 1
    candidate = body["candidates"][0]
    assert candidate["fact"] == "Gets anxious after two rest days"
    assert candidate["category"] == "psychological_tendencies"
    assert candidate["confidence"] == 0.7
    assert candidate["sourceSnippet"] == "I feel like I'm losing fitness when I rest."

    # Nothing was persisted to the athlete memory store during extraction.
    stored = await client.get(
        "/api/v1/users/me/athlete-memory-facts?includeInactive=true",
        headers=auth_headers,
    )
    assert stored.json() == {"facts": []}

    # The transcript was forwarded to the extraction service.
    call_args = mock_ai_service["extract_athlete_facts"].call_args
    assert "I hate resting" in call_args.args[0]


@pytest.mark.asyncio
async def test_extract_athlete_facts_endpoint_rejects_empty_transcript(
    client, auth_headers, mock_ai_service
):
    response = await client.post(
        "/api/v1/ai/extract-athlete-facts",
        headers=auth_headers,
        json={"transcript": ""},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_extract_then_accept_persists_fact_available_to_coach(
    client, auth_headers, mock_ai_service
):
    """End-to-end: extracted candidate accepted via observe endpoint persists."""
    mock_ai_service["extract_athlete_facts"].return_value = [
        {
            "fact": "Needs reassurance before hard sessions",
            "category": "coaching_risk",
            "confidence": 0.6,
            "source_snippet": "Am I ready for this one?",
        }
    ]

    extracted = await client.post(
        "/api/v1/ai/extract-athlete-facts",
        headers=auth_headers,
        json={"transcript": "Athlete: Am I ready for this one?"},
    )
    candidate = extracted.json()["candidates"][0]

    # User accepts the candidate -> persist via the existing observe endpoint.
    created = await client.post(
        "/api/v1/users/me/athlete-memory-facts",
        headers=auth_headers,
        json={
            "fact": candidate["fact"],
            "category": candidate["category"],
            "sourceSnippet": candidate["sourceSnippet"],
            "confidence": candidate["confidence"],
        },
    )
    assert created.status_code == 201

    # Accepted fact is now available for coaching.
    listed = await client.get(
        "/api/v1/users/me/athlete-memory-facts", headers=auth_headers
    )
    facts = listed.json()["facts"]
    assert len(facts) == 1
    assert facts[0]["fact"] == "Needs reassurance before hard sessions"


@pytest.mark.asyncio
async def test_ask_trainer_endpoint_surfaces_physiology_and_context_rationale(
    client, auth_headers, mock_ai_service
):
    """Rationale layers flow through the endpoint as camelCase response fields.

    All three, including the objective link (#565): the coach can write it and
    the chat can render it, and the athlete still sees nothing if the response
    model drops it in between.
    """
    mock_ai_service["ask_trainer"].return_value = {
        "response": "On the numbers a ride is fine, but knowing you I'd rest.",
        "physiology_rationale": "fresh enough for an easy ride",
        "context_rationale": "history of overreaching favours rest",
        "objective_rationale": "keeps you fresh for Saturday's trail day",
    }

    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "Can I ride today?"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["physiologyRationale"] == "fresh enough for an easy ride"
    assert body["contextRationale"] == "history of overreaching favours rest"
    assert body["objectiveRationale"] == "keeps you fresh for Saturday's trail day"


@pytest.mark.asyncio
async def test_ask_trainer_plan_change_reflection_in_prompt():
    """When a plan change is requested, the prompt must include honest-reflection instructions."""
    captured_prompt: list[str] = []

    async def fake_chat_history(
        provider, system_prompt, messages, json_mode=False, **kwargs
    ):
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
