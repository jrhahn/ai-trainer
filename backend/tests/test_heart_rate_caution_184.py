"""A medication that blunts the heart rate turns heart-rate targets off (ai-trainer-ops#184)."""

from datetime import timedelta
import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import select

import models
from services.dates import app_today
from services.heart_rate_caution import (
    prompt_profile,
    says_blunted,
    says_stopped,
    sets_heart_rate_target,
    without_heart_rate_targets,
)
from tests.conftest import TestSessionLocal

ASK = "/api/v1/ai/ask-trainer"
WERNER = "Ich nehme Betablocker gegen Bluthochdruck. Mein Puls geht kaum noch über 140."


@pytest.mark.parametrize(
    "message",
    [
        WERNER,
        "I take bisoprolol for my blood pressure.",
        "Seit März nehme ich Metoprolol.",
        "My heart rate won't go above 130 anymore.",
        "Mein Puls kommt nicht mehr über 135.",
    ],
)
def test_a_blunted_heart_rate_is_heard(message):
    assert says_blunted(message)
    assert not says_stopped(message)


@pytest.mark.parametrize(
    "message",
    [
        "Does a beta blocker affect training?",
        "My father takes beta blockers.",
        "Ich nehme keine Betablocker.",
        "I don't take beta blockers.",
        "My HR was 150 today.",
        "Ich nehme keine Betablocker mehr.",
    ],
)
def test_no_blunted_heart_rate(message):
    assert not says_blunted(message)


@pytest.mark.parametrize(
    "message", ["Ich nehme keine Betablocker mehr.", "I stopped taking metoprolol.", "Den Betablocker habe ich abgesetzt."]
)
def test_stopping_is_heard(message):
    assert says_stopped(message)


BPM_DAY = {
    "date": "2099-01-01",
    "workoutType": "endurance",
    "title": "Endurance",
    "description": "Easy spin. Keep your heart rate under 132 bpm. Stay relaxed.",
    "targetHeartRate": {"low": 100, "high": 132},
    "keyFocusPoints": ["Hold 133–145 bpm on the climb", "Cadence around 90"],
    "intervals": [{"duration": 60, "heartRate": 140, "rest": 60}],
    "durationMinutes": 60,
}


def test_every_heart_rate_target_goes_and_the_rest_stays():
    cleaned = without_heart_rate_targets(BPM_DAY)

    assert "targetHeartRate" not in cleaned
    assert cleaned["description"] == "Easy spin. Stay relaxed."
    assert cleaned["keyFocusPoints"] == ["Cadence around 90"]
    assert cleaned["intervals"] == [{"duration": 60, "rest": 60}]
    assert cleaned["durationMinutes"] == 60
    assert sets_heart_rate_target(BPM_DAY) and not sets_heart_rate_target(cleaned)


def test_the_prompts_get_no_max_hr_while_it_holds():
    profile = {"maxHeartRate": 165, "currentFTP": 200}
    assert prompt_profile(profile, False) == profile
    held = prompt_profile(profile, True)
    assert "maxHeartRate" not in held
    assert "no heart-rate targets" in held["heartRateTargets"]


async def _user() -> models.User:
    async with TestSessionLocal() as db:
        return await db.scalar(select(models.User))


async def _seed_plan(days: list[dict]) -> None:
    user = await _user()
    async with TestSessionLocal() as db:
        db.add(models.TrainingPlan(user_id=user.id, plan=days))
        await db.commit()


def _ahead(offset: int, **fields) -> dict:
    return {**BPM_DAY, "date": (app_today() + timedelta(days=offset)).isoformat(), **fields}


@pytest.mark.asyncio
async def test_werners_message_removes_the_targets_from_his_plan_at_once(client, auth_headers, mock_ai_service):
    await _seed_plan([_ahead(1), _ahead(3, title="Intervals")])

    response = await client.post(ASK, headers=auth_headers, json={"question": WERNER})

    chips = response.json()["capturedStates"]
    assert {"id": "heart-rate-caution", "label": "Heart-rate targets are off: your plan steers by effort and power", "removable": True} in chips
    user = await _user()
    assert user.heart_rate_targets_off is True
    async with TestSessionLocal() as db:
        plan = (await db.scalar(select(models.TrainingPlan))).plan
        history = (await db.scalars(select(models.PlanDayHistory.source))).all()
    assert plan and not any(sets_heart_rate_target(day) for day in plan)
    assert "heart_rate_caution" in history
    # The coach of this very turn already saw the plan without them.
    seen_plan = mock_ai_service["ask_trainer"].await_args.args[1]
    assert not any(sets_heart_rate_target(day) for day in seen_plan)


@pytest.mark.asyncio
async def test_a_plan_written_while_it_holds_sets_no_heart_rate_target(client, auth_headers, mock_ai_service):
    async with TestSessionLocal() as db:
        row = await db.scalar(select(models.User))
        row.heart_rate_targets_off = True
        await db.commit()
    mock_ai_service["generate_training_plan"].return_value = [_ahead(1), _ahead(2)]

    assert (await client.post("/api/v1/ai/generate-plan", headers=auth_headers, json={})).status_code == 200

    async with TestSessionLocal() as db:
        plan = (await db.scalar(select(models.TrainingPlan))).plan
    assert plan and not any(sets_heart_rate_target(day) for day in plan)
    profile = mock_ai_service["generate_training_plan"].await_args.args[0]
    assert "maxHeartRate" not in profile and "heartRateTargets" in profile


@pytest.mark.asyncio
async def test_remove_and_stopping_bring_heart_rate_back_for_new_sessions(client, auth_headers, mock_ai_service):
    await client.post(ASK, headers=auth_headers, json={"question": WERNER})
    assert (await client.delete("/api/v1/users/me/states/heart-rate-caution", headers=auth_headers)).status_code == 200
    assert (await _user()).heart_rate_targets_off is False

    await client.post(ASK, headers=auth_headers, json={"question": WERNER})
    response = await client.post(ASK, headers=auth_headers, json={"question": "Ich nehme keine Betablocker mehr."})
    assert {"id": "heart-rate-caution", "label": "Heart-rate targets can come back in new sessions", "removable": False} in response.json()["capturedStates"]
    assert (await _user()).heart_rate_targets_off is False


@pytest.mark.asyncio
async def test_without_the_flag_a_plan_keeps_its_heart_rate_targets(client, auth_headers, mock_ai_service):
    mock_ai_service["generate_training_plan"].return_value = [_ahead(1)]
    await client.post("/api/v1/ai/generate-plan", headers=auth_headers, json={})
    async with TestSessionLocal() as db:
        plan = (await db.scalar(select(models.TrainingPlan))).plan
    assert any(sets_heart_rate_target(day) for day in plan)


def test_the_migration_adds_the_flag_once(tmp_path):
    path = Path(__file__).parent.parent / "alembic/versions/20261021_000003_heart_rate_targets_off.py"
    spec = importlib.util.spec_from_file_location("_hr_caution_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'users.db'}")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY)"))

    def run(direction: str) -> set[str]:
        with engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                getattr(migration, direction)()
        return {c["name"] for c in sa.inspect(engine).get_columns("users")}

    assert "heart_rate_targets_off" in run("upgrade")
    assert "heart_rate_targets_off" in run("upgrade")
    assert "heart_rate_targets_off" not in run("downgrade")
    assert "heart_rate_targets_off" not in run("downgrade")


# --- Review of #998 ----------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    ["Mein Puls soll nicht über 150 gehen.", "I want my HR not to go over 150 in zone 2.", "Keep my heart rate under 140, please."],
)
def test_a_limit_the_athlete_wants_is_no_medication(message):
    assert not says_blunted(message)


@pytest.mark.parametrize(
    "message",
    [
        "Mein Puls bleibt kaum über 130.",
        "Mein Puls bleibt bei Belastung nicht über 140.",
        "My HR will not go over 140 any more.",
    ],
)
def test_a_state_with_a_verb_like_a_wish_is_still_heard(message):
    assert says_blunted(message)


@pytest.mark.parametrize(
    "message",
    [
        "Mein Puls geht kaum noch über 140, ich möchte trotzdem intensiv trainieren.",
        "I want to train hard, but my pulse barely goes over 140.",
        "I want a plan; my pulse barely goes over 140.",
    ],
)
def test_a_wish_about_something_else_leaves_the_statement(message):
    assert says_blunted(message)


def test_a_wish_before_the_comma_still_counts():
    assert not says_blunted("Ich möchte, dass mein Puls kaum über 140 geht.")


@pytest.mark.parametrize("text", ["Ride 1 hr at 200 W.", "Then 2 hr at 85 rpm.", "1.5 hr at around 150 W."])
def test_hours_are_no_heart_rate(text):
    assert not sets_heart_rate_target({"description": text})


def test_switching_drugs_is_not_stopping():
    message = "I stopped metoprolol, now I take bisoprolol."
    assert says_blunted(message)
    assert not says_stopped(message)


@pytest.mark.parametrize(
    "text",
    [
        "Keep HR below 130.",
        "HF unter 130 halten.",
        "Heart rate between 120 and 135.",
        "Ride at 70 % of max HR.",
        "Fahre bei 75 % der max. HF.",
        "Stay in Zone 2 heart rate.",
        "Hold HR zone 2.",
    ],
)
def test_more_heart_rate_targets_are_read_and_removed(text):
    day = {"description": f"Easy ride. {text} Enjoy it."}
    assert sets_heart_rate_target(day)
    assert without_heart_rate_targets(day)["description"] == "Easy ride. Enjoy it."


@pytest.mark.parametrize("text", ["Ride 2 hours at 180 W.", "Keep cadence above 85.", "Zone 2 power, 150–170 W."])
def test_power_and_cadence_stay(text):
    assert not sets_heart_rate_target({"description": text})


def test_an_empty_target_is_no_target():
    assert not sets_heart_rate_target({"targetHeartRate": None, "description": "Easy."})


@pytest.mark.asyncio
async def test_stopping_without_the_flag_shows_nothing(client, auth_headers, mock_ai_service):
    response = await client.post(ASK, headers=auth_headers, json={"question": "Ich nehme keine Betablocker mehr."})
    assert not any(c["id"] == "heart-rate-caution" for c in response.json()["capturedStates"])
    assert (await _user()).heart_rate_targets_off is False


@pytest.mark.asyncio
async def test_remove_twice_is_still_removed(client, auth_headers):
    for _ in range(2):
        response = await client.delete("/api/v1/users/me/states/heart-rate-caution", headers=auth_headers)
        assert response.status_code == 200
    assert (await _user()).heart_rate_targets_off is False


@pytest.mark.asyncio
async def test_a_pinned_day_loses_its_heart_rate_target_too(client, auth_headers, mock_ai_service):
    await _seed_plan([_ahead(1, source="user")])

    await client.post(ASK, headers=auth_headers, json={"question": WERNER})

    async with TestSessionLocal() as db:
        plan = (await db.scalar(select(models.TrainingPlan))).plan
    assert plan and not any(sets_heart_rate_target(day) for day in plan)


@pytest.mark.asyncio
async def test_a_failed_strip_keeps_the_flag_and_the_chip(client, auth_headers, mock_ai_service, monkeypatch):
    from services import plan_pipeline

    async def broken(*args, **kwargs):
        raise RuntimeError("database hiccup")

    monkeypatch.setattr(plan_pipeline, "commit_plan", broken)
    await _seed_plan([_ahead(1)])

    response = await client.post(ASK, headers=auth_headers, json={"question": WERNER})

    assert response.status_code == 200
    assert any(c["id"] == "heart-rate-caution" for c in response.json()["capturedStates"])
    assert (await _user()).heart_rate_targets_off is True
    # Nothing was stripped, so the reply sends no plan as if it had been (#226).
    assert response.json().get("updatedPlan") is None


# --- Persona run 5, 2026-10-24 (ai-trainer-ops#226, #227) --------------------

# Werner's two tempo days as the model wrote them, and as they must read once
# the heart rate is gone: the bpm went, and with it the whole workout (#227).
WERNER_TEMPO = [
    (
        "Ride for 75 minutes total, including a warm-up, two 12-minute blocks at 180–195 W "
        "(Tempo, 75–80% of your 245 W FTP, keeping heart rate between 131–145 bpm), with 5 "
        "minutes of easy spinning between blocks, and an easy cool-down. This introduces "
        "controlled intensity.",
        "Ride for 75 minutes total, including a warm-up, two 12-minute blocks at 180–195 W "
        "(Tempo, 75–80% of your 245 W FTP), with 5 minutes of easy spinning between blocks, "
        "and an easy cool-down. This introduces controlled intensity.",
    ),
    (
        "Ride for 80 minutes total, featuring two 15-minute intervals at 180–195 W (Tempo, "
        "75–80% of your 245 W FTP, heart rate 131–145 bpm), with 5 minutes of easy spinning "
        "between them, bookended by a warm-up and cool-down.",
        "Ride for 80 minutes total, featuring two 15-minute intervals at 180–195 W (Tempo, "
        "75–80% of your 245 W FTP), with 5 minutes of easy spinning between them, bookended "
        "by a warm-up and cool-down.",
    ),
]


@pytest.mark.parametrize(("description", "expected"), WERNER_TEMPO)
def test_a_session_keeps_its_blocks_when_its_heart_rate_goes(description, expected):
    cleaned = without_heart_rate_targets({"description": description, "durationMinutes": 75})

    assert cleaned["description"] == expected
    assert not sets_heart_rate_target(cleaned)


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("Ride for 90 minutes at 145–170 W, keeping your heart rate under 130 bpm.", "Ride for 90 minutes at 145–170 W."),
        ("Keeping your heart rate under 130 bpm, ride for 90 minutes at 145–170 W.", "Ride for 90 minutes at 145–170 W."),
        ("Keep HR under 130 bpm, and enjoy the ride.", "Enjoy the ride."),
        ("Spin easy (Zone 2 heart rate). Smile.", "Spin easy. Smile."),
        ("Fahre 1,5 h bei 150 W, Puls unter 130 Schlägen.", "Fahre 1,5 h bei 150 W."),
        ("Then, keep HR under 130 bpm. Stay relaxed.", "Stay relaxed."),
        ("Ride easy (1,5 h, under 130 bpm).", "Ride easy (1,5 h)."),
    ],
)
def test_only_the_part_with_the_heart_rate_goes(description, expected):
    assert without_heart_rate_targets({"description": description})["description"] == expected


def test_a_focus_point_keeps_what_is_not_heart_rate():
    day = {"keyFocusPoints": ["Hold 133–145 bpm on the climb", "Keep cadence 85–95 rpm, HR under 140 bpm", None]}

    assert without_heart_rate_targets(day)["keyFocusPoints"] == ["Keep cadence 85–95 rpm", None]


def test_a_target_in_the_main_clause_takes_the_blocks_from_the_fields():
    """No clause to cut the bpm from: the sentence goes, and the session's
    length, blocks and watts come back from its fields, never invented."""
    day = {
        "description": "Warm up, then hold 2 x 12 min at 180–195 W and 131–145 bpm.",
        "durationMinutes": 75,
        "targetPower": {"low": 180, "high": 195},
        "intervals": [{"duration": 720, "power": 187, "heartRate": 140, "rest": 300}] * 2,
    }

    cleaned = without_heart_rate_targets(day)

    assert cleaned["description"] == "75 min: 2 × 12 min at 187 W, 5 min easy between. Warm up."
    assert without_heart_rate_targets(cleaned) == cleaned


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        ({"durationMinutes": 60, "targetPower": {"low": 140, "high": 160}}, "60 min at 140–160 W."),
        ({"durationMinutes": 60, "targetPower": {"low": 150, "high": 150}}, "60 min at 150 W."),
        ({"durationMinutes": 105, "durationMinMinutes": 90, "durationMaxMinutes": 120}, "90–120 min."),
        (
            {"intervals": [{"duration": 30, "pace": "4:30/km", "rest": 90}] * 3 + [{"duration": 600, "rest": 0}]},
            "3 × 30 s at 4:30/km, 90 s easy between, then 10 min.",
        ),
        (
            {"durationMinutes": 40, "targetPower": {"low": 250, "high": 270}, "intervals": [{"duration": 1200, "rest": 300}, "?", {"duration": True}]},
            "40 min: 20 min at 250–270 W, 5 min easy after.",
        ),
        ({}, ""),
    ],
)
def test_a_description_left_empty_is_said_from_the_fields(day, expected):
    cleaned = without_heart_rate_targets({**day, "description": "Keep your heart rate under 130 bpm."})

    assert cleaned["description"] == expected


def test_words_without_a_length_get_nothing_added():
    day = {"description": "Heart rate between 120 and 135. Enjoy it.", "durationMinutes": 60}

    assert without_heart_rate_targets(day)["description"] == "Enjoy it."


@pytest.mark.asyncio
async def test_the_reply_carries_the_plan_without_the_targets(client, auth_headers, mock_ai_service):
    """The strip was saved, not sent: the screen kept "Puls unter 130 bpm"
    under a reply that had just set heart rate aside, until the next load (#226)."""
    await _seed_plan([_ahead(1), _ahead(3, title="Intervals")])

    response = await client.post(ASK, headers=auth_headers, json={"question": WERNER})

    updated = response.json()["updatedPlan"]
    assert [day["title"] for day in updated] == ["Endurance", "Intervals"]
    assert not any(sets_heart_rate_target(day) for day in updated)
    assert updated[0]["description"] == "Easy spin. Stay relaxed."


@pytest.mark.asyncio
async def test_a_reply_that_stripped_nothing_carries_no_plan(client, auth_headers, mock_ai_service):
    await _seed_plan([_ahead(1, description="Easy spin.", targetHeartRate=None, keyFocusPoints=[], intervals=None)])

    response = await client.post(ASK, headers=auth_headers, json={"question": WERNER})

    assert response.json().get("updatedPlan") is None


@pytest.mark.asyncio
async def test_a_coach_change_in_the_same_reply_keeps_the_strip(client, auth_headers, mock_ai_service):
    await _seed_plan([_ahead(1), _ahead(3, title="Intervals")])
    later = _ahead(3)["date"]
    mock_ai_service["ask_trainer"].return_value = {
        "response": "Den Donnerstag habe ich ruhiger gemacht.",
        "plan_updates": [
            {"date": later, "workoutType": "endurance", "title": "Easy Spin", "description": "Spin easy for 60 minutes.", "durationMinutes": 60}
        ],
        "sources": [],
    }

    response = await client.post(ASK, headers=auth_headers, json={"question": WERNER})

    updated = response.json()["updatedPlan"]
    assert {day["date"]: day["title"] for day in updated}[later] == "Easy Spin"
    assert not any(sets_heart_rate_target(day) for day in updated)
