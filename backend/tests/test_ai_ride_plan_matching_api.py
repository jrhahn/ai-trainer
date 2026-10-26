"""Matching a ride to its planned session: automatic, ambiguous, chosen by the athlete, and mislabelled.

Split out of ``test_ai.py`` (ai-trainer-ops#259); its history is there.
"""


import pytest



@pytest.mark.asyncio
async def test_analyse_activities_auto_matches_single_planned_ride(
    client, auth_headers, mock_ai_service
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
                    "date": "2026-05-04",
                    "workoutType": "endurance",
                    "title": "Endurance Ride",
                    "description": "Steady Z2",
                    "durationMinutes": 90,
                }
            ],
        )
        await db.commit()

    response = await client.post(
        "/api/v1/ai/analyse-activities",
        headers=auth_headers,
        json={
            "activities": [
                {
                    "id": 61001,
                    "name": "Morning endurance",
                    "type": "Ride",
                    "distance": 40000,
                    "movingTime": 3600,
                    "elapsedTime": 3600,
                    "totalElevationGain": 300,
                    "startDate": "2026-05-04T08:00:00Z",
                }
            ]
        },
    )
    assert response.status_code == 200

    history = await client.get("/api/v1/users/me/ride-metrics-history", headers=auth_headers)
    ride = next(r for r in history.json()["rides"] if r["stravaActivityId"] == 61001)
    assert ride["planMatchStatus"] == "auto_matched"
    assert ride["matchedPlanDate"] == "2026-05-04"
    assert ride["matchedPlanSnapshot"]["title"] == "Endurance Ride"
    assert ride["activityName"] == "Morning endurance"
    mock_ai_service["rate_completed_workout"].assert_called()


@pytest.mark.asyncio
async def test_analyse_activities_marks_multiple_same_day_rides_ambiguous(
    client, auth_headers, mock_ai_service
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
                    "date": "2026-05-05",
                    "workoutType": "intervals",
                    "title": "Threshold Intervals",
                    "description": "4x8 min",
                    "durationMinutes": 75,
                }
            ],
        )
        await db.commit()

    response = await client.post(
        "/api/v1/ai/analyse-activities",
        headers=auth_headers,
        json={
            "activities": [
                {
                    "id": 62001,
                    "name": "Commute",
                    "type": "Ride",
                    "distance": 8000,
                    "movingTime": 1200,
                    "elapsedTime": 1200,
                    "totalElevationGain": 50,
                    "startDate": "2026-05-05T07:00:00Z",
                },
                {
                    "id": 62002,
                    "name": "Workout",
                    "type": "Ride",
                    "distance": 45000,
                    "movingTime": 3600,
                    "elapsedTime": 3600,
                    "totalElevationGain": 300,
                    "startDate": "2026-05-05T18:00:00Z",
                },
            ]
        },
    )
    assert response.status_code == 200

    history = await client.get("/api/v1/users/me/ride-metrics-history", headers=auth_headers)
    rides = [r for r in history.json()["rides"] if r["activityDate"] == "2026-05-05"]
    assert {r["planMatchStatus"] for r in rides} == {"ambiguous"}
    assert {r["matchedPlanSnapshot"]["title"] for r in rides} == {"Threshold Intervals"}
    mock_ai_service["rate_completed_workout"].assert_not_called()


@pytest.mark.asyncio
async def test_resolve_ride_match_selects_one_and_unmatches_siblings(
    client, auth_headers, mock_ai_service
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
                    "date": "2026-05-06",
                    "workoutType": "tempo",
                    "title": "Tempo Ride",
                    "description": "Controlled tempo",
                    "durationMinutes": 80,
                }
            ],
        )
        await crud.upsert_ride_metric(db, user_id, strava_activity_id=63001, activity_date="2026-05-06")
        await crud.upsert_ride_metric(db, user_id, strava_activity_id=63002, activity_date="2026-05-06")
        await db.commit()
        auto = await crud.get_ride_metrics_by_activity_ids(db, user_id, [63001, 63002])
        assert len(auto) == 2
        # Apply matching after both rides exist.
        from services.ride_matching import apply_ride_plan_matches

        await apply_ride_plan_matches(
            db,
            user_id,
            [
                {
                    "date": "2026-05-06",
                    "workoutType": "tempo",
                    "title": "Tempo Ride",
                    "description": "Controlled tempo",
                    "durationMinutes": 80,
                }
            ],
            [63001, 63002],
        )
        await db.commit()

    response = await client.post(
        "/api/v1/ai/resolve-ride-match",
        headers=auth_headers,
        json={"plannedDate": "2026-05-06", "stravaActivityId": 63002},
    )
    assert response.status_code == 200
    assert response.json()["ride"]["planMatchStatus"] == "manual_matched"

    history = await client.get("/api/v1/users/me/ride-metrics-history", headers=auth_headers)
    by_id = {r["stravaActivityId"]: r for r in history.json()["rides"]}
    assert by_id[63002]["planMatchStatus"] == "manual_matched"
    assert by_id[63001]["planMatchStatus"] == "unmatched"
    mock_ai_service["rate_completed_workout"].assert_called()


@pytest.mark.asyncio
async def test_resolve_ride_match_honours_the_requested_session(
    client, auth_headers, mock_ai_service
):
    """The endpoint carried no slot, so it could only ever resolve to the first
    session of the date — on a two-a-day, the wrong one (#547).

    ``matched_plan_slot`` is not exposed by ``RideMetricSchema``, so the check
    goes to the row.
    """
    import crud
    import models
    from auth import decode_token
    from tests.conftest import TestSessionLocal

    token = auth_headers["Authorization"].split(" ", 1)[1]
    user_id = decode_token(token)
    date = "2026-05-07"
    plan = [
        {
            "date": date,
            "slot": 0,
            "workoutType": "endurance",
            "title": "Morning Endurance",
            "durationMinutes": 90,
        },
        {
            "date": date,
            "slot": 1,
            "workoutType": "intervals",
            "title": "Evening Intervals",
            "durationMinutes": 60,
        },
    ]

    async with TestSessionLocal() as db:
        await crud.upsert_training_plan(db, user_id, plan)
        await crud.upsert_ride_metric(
            db, user_id, strava_activity_id=64001, activity_date=date
        )
        await db.commit()

    response = await client.post(
        "/api/v1/ai/resolve-ride-match",
        headers=auth_headers,
        json={
            "plannedDate": date,
            "stravaActivityId": 64001,
            "plannedSlot": 1,
        },
    )
    assert response.status_code == 200

    async with TestSessionLocal() as db:
        rides = await crud.get_ride_metrics_by_date(db, user_id, date)
    ride = next(r for r in rides if r.strava_activity_id == 64001)
    assert isinstance(ride, models.RideMetric)
    assert ride.matched_plan_slot == 1
    assert ride.matched_plan_snapshot["workoutType"] == "intervals"


@pytest.mark.asyncio
async def test_resolve_ride_match_marks_only_the_named_session_completed(
    client, auth_headers, mock_ai_service
):
    """The athlete answering "that was the evening one" is a completion (#574).

    ``mark_matched_days_completed`` only ever counted auto matches, so resolving an
    ambiguity left the day looking untouched: no calendar tick, and no protection
    against an automated regenerate dropping a session that was actually ridden.
    The morning session must stay open — it was not what the athlete named.
    """
    import crud
    import schemas
    from auth import decode_token
    from tests.conftest import TestSessionLocal

    token = auth_headers["Authorization"].split(" ", 1)[1]
    user_id = decode_token(token)
    date = "2026-05-08"
    plan = [
        {
            "date": date,
            "slot": 0,
            "workoutType": "endurance",
            "title": "Morning Endurance",
            "durationMinutes": 90,
        },
        {
            "date": date,
            "slot": 1,
            "workoutType": "intervals",
            "title": "Evening Intervals",
            "durationMinutes": 60,
        },
    ]

    async with TestSessionLocal() as db:
        await crud.upsert_training_plan(db, user_id, plan)
        await crud.upsert_ride_metric(
            db, user_id, strava_activity_id=64101, activity_date=date
        )
        await db.commit()

    response = await client.post(
        "/api/v1/ai/resolve-ride-match",
        headers=auth_headers,
        json={
            "plannedDate": date,
            "stravaActivityId": 64101,
            "plannedSlot": 1,
        },
    )
    assert response.status_code == 200

    async with TestSessionLocal() as db:
        stored = await crud.get_training_plan(db, user_id)
    # Slot 0 is never serialized (#496), so the slot has to come from the helper.
    sessions = {
        schemas.day_slot(day): day for day in stored.plan if day["date"] == date
    }
    assert sessions[1].get("completed") is True
    assert not sessions[0].get("completed")


@pytest.mark.asyncio
async def test_analyse_activities_without_plan_leaves_ride_unmatched(
    client, auth_headers, mock_ai_service
):
    response = await client.post(
        "/api/v1/ai/analyse-activities",
        headers=auth_headers,
        json={
            "activities": [
                {
                    "id": 65001,
                    "name": "Free ride",
                    "type": "Ride",
                    "distance": 40000,
                    "movingTime": 3600,
                    "elapsedTime": 3600,
                    "totalElevationGain": 300,
                    "startDate": "2026-05-09T08:00:00Z",
                }
            ]
        },
    )
    assert response.status_code == 200
    history = await client.get("/api/v1/users/me/ride-metrics-history", headers=auth_headers)
    ride = next(r for r in history.json()["rides"] if r["stravaActivityId"] == 65001)
    assert ride["planMatchStatus"] == "unmatched"


@pytest.mark.asyncio
async def test_apply_ride_plan_matches_sets_mismatch_label_for_gross_duration_deviation(
    client, auth_headers
):
    """A ride that is >2.5× longer than the planned duration gets label_override='Mismatch'."""
    import crud
    from auth import decode_token
    from tests.conftest import TestSessionLocal
    from services.ride_matching import apply_ride_plan_matches

    token = auth_headers["Authorization"].split(" ", 1)[1]
    user_id = decode_token(token)

    plan = [
        {
            "date": "2026-05-10",
            "workoutType": "recovery",
            "title": "Active Recovery",
            "description": "Easy spin",
            "durationMinutes": 45,
        }
    ]

    async with TestSessionLocal() as db:
        await crud.upsert_training_plan(db, user_id, plan)
        # 5h 25m ride — 325 min, 7.2× the planned 45 min
        await crud.upsert_ride_metric(
            db,
            user_id,
            strava_activity_id=70001,
            activity_date="2026-05-10",
            duration_seconds=19500,
        )
        # 42-min ride — within the normal range (ratio ≈ 0.93, no mismatch expected)
        await crud.upsert_ride_metric(
            db,
            user_id,
            strava_activity_id=70002,
            activity_date="2026-05-11",
            duration_seconds=2520,
        )
        await crud.upsert_training_plan(
            db,
            user_id,
            plan
            + [
                {
                    "date": "2026-05-11",
                    "workoutType": "recovery",
                    "title": "Active Recovery",
                    "description": "Easy spin",
                    "durationMinutes": 45,
                }
            ],
        )
        await apply_ride_plan_matches(db, user_id, plan + [
            {
                "date": "2026-05-11",
                "workoutType": "recovery",
                "title": "Active Recovery",
                "description": "Easy spin",
                "durationMinutes": 45,
            }
        ], [70001, 70002])
        await db.commit()

        rides = await crud.get_ride_metrics_by_activity_ids(db, user_id, [70001, 70002])
        by_id = {r.strava_activity_id: r for r in rides}

    # Gross mismatch ride should be flagged
    assert by_id[70001].label_override == "Mismatch"
    # Well-matched ride should have no override
    assert by_id[70002].label_override is None


@pytest.mark.asyncio
async def test_apply_ride_plan_matches_accepts_a_long_day_split_by_a_stop(
    client, auth_headers
):
    """A four-hour day recorded as two files still satisfies the plan (#543).

    This used to assert the same for a morning road ride and an afternoon MTB
    ride — two separate trainings whose durations happened to add up. Summing
    now requires the recordings to be parts of one session, so the two halves
    here sit fifteen minutes apart; the separated case is pinned in
    ``test_plan_sessions.py``.
    """
    import crud
    from auth import decode_token
    from services.ride_matching import apply_ride_plan_matches
    from tests.conftest import TestSessionLocal

    token = auth_headers["Authorization"].split(" ", 1)[1]
    user_id = decode_token(token)
    plan = [
        {
            "date": "2026-06-20",
            "workoutType": "endurance",
            "title": "Long Endurance Ride",
            "durationMinutes": 240,
        }
    ]

    async with TestSessionLocal() as db:
        await crud.upsert_training_plan(db, user_id, plan)
        await crud.upsert_ride_metric(
            db,
            user_id,
            strava_activity_id=71001,
            activity_date="2026-06-20",
            activity_start_datetime="2026-06-20T08:00:00Z",
            sport_type="Ride",
            activity_name="Long Ride, Part One",
            duration_seconds=180 * 60,
        )
        await crud.upsert_ride_metric(
            db,
            user_id,
            strava_activity_id=71002,
            activity_date="2026-06-20",
            activity_start_datetime="2026-06-20T11:15:00Z",
            sport_type="MountainBikeRide",
            activity_name="Long Ride, Part Two",
            duration_seconds=60 * 60,
        )

        reviewed = await apply_ride_plan_matches(db, user_id, plan, [71001, 71002])
        rides = await crud.get_ride_metrics_by_activity_ids(db, user_id, [71001, 71002])
        by_id = {ride.strava_activity_id: ride for ride in rides}

    assert [ride.strava_activity_id for ride in reviewed] == [71001]
    assert {ride.plan_match_status for ride in by_id.values()} == {"auto_matched"}
    assert {ride.label_override for ride in by_id.values()} == {"OK"}


@pytest.mark.asyncio
async def test_apply_ride_plan_matches_labels_easy_extra_ride(
    client, auth_headers
):
    import crud
    from auth import decode_token
    from services.ride_matching import apply_ride_plan_matches
    from tests.conftest import TestSessionLocal

    token = auth_headers["Authorization"].split(" ", 1)[1]
    user_id = decode_token(token)
    plan = [
        {
            "date": "2026-06-20",
            "workoutType": "endurance",
            "title": "Long Endurance Ride with Climbing Focus",
            "durationMinutes": 180,
        }
    ]

    async with TestSessionLocal() as db:
        await crud.upsert_training_plan(db, user_id, plan)
        await crud.upsert_ride_metric(
            db,
            user_id,
            strava_activity_id=72001,
            activity_date="2026-06-20",
            sport_type="MountainBikeRide",
            activity_name="Darmstadt Mountain Biking",
            duration_seconds=63 * 60,
            intensity_factor=0.58,
            tss=32,
        )
        await crud.upsert_ride_metric(
            db,
            user_id,
            strava_activity_id=72002,
            activity_date="2026-06-20",
            sport_type="Ride",
            activity_name="Darmstadt Road Cycling",
            duration_seconds=205 * 60,
            intensity_factor=0.68,
            tss=140,
        )

        reviewed = await apply_ride_plan_matches(db, user_id, plan, [72001, 72002])
        rides = await crud.get_ride_metrics_by_activity_ids(db, user_id, [72001, 72002])
        by_id = {ride.strava_activity_id: ride for ride in rides}

    assert [ride.strava_activity_id for ride in reviewed] == [72002]
    assert by_id[72002].plan_match_status == "auto_matched"
    assert by_id[72002].label_override is None
    assert by_id[72001].plan_match_status == "unmatched"
    assert by_id[72001].matched_plan_snapshot["title"] == (
        "Long Endurance Ride with Climbing Focus"
    )
    assert by_id[72001].label_override == "Additional"


@pytest.mark.asyncio
async def test_apply_ride_plan_matches_labels_hard_extra_ride_as_too_much(
    client, auth_headers
):
    import crud
    from auth import decode_token
    from services.ride_matching import apply_ride_plan_matches
    from tests.conftest import TestSessionLocal

    token = auth_headers["Authorization"].split(" ", 1)[1]
    user_id = decode_token(token)
    plan = [
        {
            "date": "2026-06-20",
            "workoutType": "endurance",
            "title": "Long Endurance Ride",
            "durationMinutes": 180,
        }
    ]

    async with TestSessionLocal() as db:
        await crud.upsert_training_plan(db, user_id, plan)
        await crud.upsert_ride_metric(
            db,
            user_id,
            strava_activity_id=73001,
            activity_date="2026-06-20",
            sport_type="Ride",
            activity_name="Long Road Ride",
            duration_seconds=175 * 60,
            intensity_factor=0.7,
            tss=135,
        )
        await crud.upsert_ride_metric(
            db,
            user_id,
            strava_activity_id=73002,
            activity_date="2026-06-20",
            sport_type="Ride",
            activity_name="Evening Threshold Work",
            duration_seconds=70 * 60,
            intensity_factor=0.9,
            tss=95,
            ride_purpose="interval_threshold",
        )

        reviewed = await apply_ride_plan_matches(db, user_id, plan, [73001, 73002])
        rides = await crud.get_ride_metrics_by_activity_ids(db, user_id, [73001, 73002])
        by_id = {ride.strava_activity_id: ride for ride in rides}

    assert [ride.strava_activity_id for ride in reviewed] == [73001]
    assert by_id[73001].plan_match_status == "auto_matched"
    assert by_id[73002].plan_match_status == "unmatched"
    assert by_id[73002].label_override == "Too much"


@pytest.mark.asyncio
async def test_resolve_ride_match_finds_an_intervals_ride_by_its_string_id(
    client, auth_headers, mock_ai_service
):
    """The numeric id cannot survive the browser, so it cannot be the lookup key.

    Reproduced from production: three intervals.icu activities on 2026-08-09,
    every resolve answering 404 "No ambiguous ride match found" and the card
    saying "Could not save that". A synthesized 63-bit id is rounded the moment
    it becomes a JS number, so what arrives matches no row (#441).
    """
    import crud
    from auth import decode_token
    from tests.conftest import TestSessionLocal
    from services.ride_matching import apply_ride_plan_matches

    plan = [
        {
            "date": "2026-08-09",
            "workoutType": "endurance",
            "title": "Short Heat-Friendly Recovery Spin",
            "description": "Gentle recovery riding",
            "durationMinutes": 75,
        }
    ]
    # The real production ids, and what they become as float64.
    stored_id = 7846148097020609552
    corrupted_id = int(float(stored_id))
    assert corrupted_id != stored_id, "the premise: this id does not survive JS"

    token = auth_headers["Authorization"].split(" ", 1)[1]
    user_id = decode_token(token)
    async with TestSessionLocal() as db:
        await crud.upsert_training_plan(db, user_id, plan)
        await crud.upsert_ride_metric(
            db,
            user_id,
            strava_activity_id=stored_id,
            activity_source="intervals",
            external_activity_id="i174087702",
            activity_date="2026-08-09",
            duration_seconds=32 * 60,
        )
        await crud.upsert_ride_metric(
            db,
            user_id,
            strava_activity_id=1267888305910446691,
            activity_source="intervals",
            external_activity_id="i174034432",
            activity_date="2026-08-09",
            duration_seconds=34 * 60,
        )
        await db.commit()
        await apply_ride_plan_matches(
            db, user_id, plan, [stored_id, 1267888305910446691]
        )
        await db.commit()

    response = await client.post(
        "/api/v1/ai/resolve-ride-match",
        headers=auth_headers,
        json={
            "plannedDate": "2026-08-09",
            # Exactly what the browser sends: the rounded number…
            "stravaActivityId": corrupted_id,
            # …and the id that actually identifies the row.
            "externalActivityId": "i174087702",
        },
    )

    assert response.status_code == 200
    assert response.json()["ride"]["externalActivityId"] == "i174087702"
    assert response.json()["ride"]["planMatchStatus"] == "manual_matched"


@pytest.mark.asyncio
async def test_resolve_ride_match_still_works_without_a_string_id(
    client, auth_headers, mock_ai_service
):
    """Strava's own ids are well inside float64, and an older client sends no
    string id at all — the numeric lookup stays the fallback."""
    import crud
    from auth import decode_token
    from tests.conftest import TestSessionLocal
    from services.ride_matching import apply_ride_plan_matches

    plan = [
        {
            "date": "2026-05-20",
            "workoutType": "endurance",
            "title": "Endurance",
            "description": "Steady",
            "durationMinutes": 90,
        }
    ]
    token = auth_headers["Authorization"].split(" ", 1)[1]
    user_id = decode_token(token)
    async with TestSessionLocal() as db:
        await crud.upsert_training_plan(db, user_id, plan)
        for activity_id in (64001, 64002):
            await crud.upsert_ride_metric(
                db, user_id, strava_activity_id=activity_id, activity_date="2026-05-20"
            )
        await db.commit()
        await apply_ride_plan_matches(db, user_id, plan, [64001, 64002])
        await db.commit()

    response = await client.post(
        "/api/v1/ai/resolve-ride-match",
        headers=auth_headers,
        json={"plannedDate": "2026-05-20", "stravaActivityId": 64002},
    )
    assert response.status_code == 200
    assert response.json()["ride"]["planMatchStatus"] == "manual_matched"
