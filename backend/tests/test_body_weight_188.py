"""An optional body weight in the profile (ai-trainer-ops#188).

Lena asked how many watts keep her under 45 minutes on a 14 km, 850 m climb and
got "260 bis 275 Watt": about 4.3 W/kg, so right only at about 60 kg, and the
profile had no weight at all.
"""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

import models
from schemas import UserProfileSchema
from services.prompts import _COACH_VOICE_TRAITS

ME = "/api/v1/users/me"
ASK = "/api/v1/ai/ask-trainer"


@pytest.mark.asyncio
async def test_the_weight_is_stored_shown_and_removed(client, auth_headers):
    saved = await client.put(ME, headers=auth_headers, json={"weightKg": 62.5})

    assert saved.status_code == 200
    assert saved.json()["weightKg"] == 62.5
    assert (await client.get(ME, headers=auth_headers)).json()["weightKg"] == 62.5

    removed = await client.put(ME, headers=auth_headers, json={"weightKg": None})

    assert removed.status_code == 200
    assert removed.json()["weightKg"] is None


@pytest.mark.asyncio
async def test_another_change_keeps_the_weight(client, auth_headers):
    await client.put(ME, headers=auth_headers, json={"weightKg": 70})
    await client.put(ME, headers=auth_headers, json={"weeklyHours": 6})

    assert (await client.get(ME, headers=auth_headers)).json()["weightKg"] == 70


@pytest.mark.asyncio
@pytest.mark.parametrize("weight", [12, 29.9, 250.1, 600])
async def test_a_typo_is_refused(client, auth_headers, weight):
    reply = await client.put(ME, headers=auth_headers, json={"weightKg": weight})

    assert reply.status_code == 422


def _profile(**fields) -> dict:
    user = models.User(email="lena@example.com", hashed_password="x", follows_training_plan=False, **fields)
    return UserProfileSchema.from_user(user).model_dump(by_alias=True)


def test_the_coach_reads_the_weight_and_the_w_per_kg():
    profile = _profile(current_ftp=280, weight_kg=62.0)

    assert profile["bodyWeight"] == "62 kg"
    assert profile["ftpPerKg"] == "4.5 W/kg"


def test_a_weight_without_an_ftp_has_no_w_per_kg():
    profile = _profile(weight_kg=68.5)

    assert profile["bodyWeight"] == "68.5 kg"
    assert profile["ftpPerKg"] is None


def test_without_a_weight_the_profile_names_none():
    profile = _profile(current_ftp=280)

    assert profile["bodyWeight"] is None
    assert profile["ftpPerKg"] is None


@pytest.mark.asyncio
async def test_the_chat_gets_the_weight(client, auth_headers, mock_ai_service):
    await client.put(ME, headers=auth_headers, json={"weightKg": 62, "currentFTP": 280})

    await client.post(ASK, headers=auth_headers, json={"question": "Wie viel Watt am Berg für unter 45 Minuten?"})

    profile = mock_ai_service["ask_trainer"].call_args.args[2]
    assert profile["bodyWeight"] == "62 kg"
    assert profile["ftpPerKg"] == "4.5 W/kg"


def test_the_coach_uses_the_weight_or_asks_for_it():
    assert "A climb power target needs the athlete's weight" in _COACH_VOICE_TRAITS
    assert "otherwise ask for the weight or say which weight your number assumes" in _COACH_VOICE_TRAITS


def test_the_migration_adds_the_column_once(tmp_path):
    path = Path(__file__).parent.parent / "alembic/versions/20261022_000001_body_weight.py"
    spec = importlib.util.spec_from_file_location("_body_weight_migration", path)
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

    assert "weight_kg" in run("upgrade")
    assert "weight_kg" in run("upgrade")
    assert "weight_kg" not in run("downgrade")
    assert "weight_kg" not in run("downgrade")
