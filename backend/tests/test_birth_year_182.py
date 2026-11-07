"""An optional birth year in the profile (ai-trainer-ops#182).

The owner's test athlete wrote "given my age i want to stay healthy" and nobody
asked how old they were: the profile had no age at all. Recovery, a maximum
heart rate and the health questions an older athlete needs all depend on it.
"""

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

import models
from schemas import UserProfileSchema
from services.prompts import profile_plan_rules

ME = "/api/v1/users/me"
ASK = "/api/v1/ai/ask-trainer"


@pytest.mark.asyncio
async def test_the_birth_year_is_stored_shown_and_removed(client, auth_headers):
    saved = await client.put(ME, headers=auth_headers, json={"birthYear": 1958})

    assert saved.status_code == 200
    assert saved.json()["birthYear"] == 1958
    assert (await client.get(ME, headers=auth_headers)).json()["birthYear"] == 1958

    removed = await client.put(ME, headers=auth_headers, json={"birthYear": None})

    assert removed.status_code == 200
    assert removed.json()["birthYear"] is None


@pytest.mark.asyncio
async def test_another_change_keeps_the_birth_year(client, auth_headers):
    await client.put(ME, headers=auth_headers, json={"birthYear": 1970})
    await client.put(ME, headers=auth_headers, json={"weeklyHours": 6})

    assert (await client.get(ME, headers=auth_headers)).json()["birthYear"] == 1970


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "year",
    [1919, datetime.now(timezone.utc).year - 9, datetime.now(timezone.utc).year, 19580],
)
async def test_a_typo_is_refused(client, auth_headers, year):
    reply = await client.put(ME, headers=auth_headers, json={"birthYear": year})

    assert reply.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("year", [1920, datetime.now(timezone.utc).year - 10])
async def test_the_outer_years_are_kept(client, auth_headers, year):
    reply = await client.put(ME, headers=auth_headers, json={"birthYear": year})

    assert reply.status_code == 200


def _profile(**fields) -> dict:
    user = models.User(
        email="werner@example.com",
        hashed_password="x",
        follows_training_plan=False,
        **fields,
    )
    return UserProfileSchema.from_user(user).model_dump(by_alias=True)


def test_the_coach_reads_the_age_with_the_year():
    profile = _profile(birth_year=1958)

    assert profile["age"] == f"{datetime.now(timezone.utc).year - 1958} (born 1958)"


def test_without_a_birth_year_the_profile_names_no_age():
    assert _profile()["age"] is None


@pytest.mark.asyncio
async def test_the_chat_gets_the_age(client, auth_headers, mock_ai_service):
    await client.put(ME, headers=auth_headers, json={"birthYear": 1960})

    await client.post(
        ASK, headers=auth_headers, json={"question": "Wie viel Erholung brauche ich?"}
    )

    profile = mock_ai_service["ask_trainer"].call_args.args[2]
    assert profile["age"].endswith("(born 1960)")


def test_the_plan_rules_read_the_age_and_never_assume_one():
    assert "'age' (ai-trainer-ops#182)" in profile_plan_rules({"age": "58 (born 1968)"})
    assert "From about 50, recovery from hard sessions takes longer" in (
        profile_plan_rules({"age": "58 (born 1968)"})
    )
    assert "Never assume an age the profile does not give." in profile_plan_rules(
        {"age": "58 (born 1968)"}
    )


def test_the_migration_adds_the_column_once(tmp_path):
    path = (
        Path(__file__).parent.parent / "alembic/versions/20261107_000001_birth_year.py"
    )
    spec = importlib.util.spec_from_file_location("_birth_year_migration", path)
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

    assert "birth_year" in run("upgrade")
    assert "birth_year" in run("upgrade")
    assert "birth_year" not in run("downgrade")
    assert "birth_year" not in run("downgrade")
