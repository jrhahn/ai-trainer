"""Deletion that is complete, and an export that is (ai-trainer-ops#6).

Both halves walk the schema instead of naming tables. A list of tables in a test
is complete on the day it is written; the next migration adds a table the list
does not know, and the test stays green while deletion leaves that table's rows
behind. Here, a new table with a foreign key to ``users`` is seeded, deleted and
exported without anyone touching this file — or it fails.

SQLite does not enforce foreign keys unless asked, which is why the suite never
saw what PostgreSQL does: refuse to delete a user any child row still points
at. The deletion tests switch enforcement on so that a missing cascade fails
the way it fails in production, instead of leaving a quiet orphan behind.
"""

import uuid
from datetime import date, datetime, timezone

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    Integer,
    Numeric,
    String,
    Text,
    Table,
    event,
    func,
    inspect,
    select,
)

import models
from database import Base
from services import account_export
from tests.conftest import TestSessionLocal, test_engine

_PASSWORD = "Str0ng!Pass"
_SECRET_MARKER = "do-not-export-7f3a"


def _enable_foreign_keys(dbapi_connection, _record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


@pytest_asyncio.fixture
async def foreign_keys_enforced():
    """Make SQLite refuse an orphan the way PostgreSQL does.

    The pragma is per connection, so the pool is emptied on the way in (every
    connection the test opens runs the listener) and on the way out (no
    enforcing connection leaks into the next test).
    """
    event.listen(test_engine.sync_engine, "connect", _enable_foreign_keys)
    await test_engine.dispose()
    try:
        yield
    finally:
        event.remove(test_engine.sync_engine, "connect", _enable_foreign_keys)
        await test_engine.dispose()


def _value_for(column, marker: str):
    """A value the column accepts, for columns the database will not fill."""
    kind = column.type
    if isinstance(kind, models.EncryptedString):
        return marker
    if isinstance(kind, Boolean):
        return False
    if isinstance(kind, (Integer, BigInteger)):
        return 0
    if isinstance(kind, (Float, Numeric)):
        return 0.0
    if isinstance(kind, DateTime):
        return datetime.now(timezone.utc)
    if isinstance(kind, Date):
        return date.today()
    if isinstance(kind, JSON):
        return {}
    if isinstance(kind, (String, Text)):
        value = marker if column.unique is not True else uuid.uuid4().hex
        length = getattr(kind, "length", None)
        return value[:length] if length else value
    raise AssertionError(
        f"{column.table.name}.{column.name}: no seed value for {kind!r} — "
        "teach _value_for this type"
    )


def _seed_row(table: Table, user_id: str, marker: str = "seeded") -> dict:
    row = {}
    owner = account_export.owner_column(table)
    for column in table.columns:
        if column is owner:
            row[column.name] = user_id
        elif account_export.is_secret(table, column):
            row[column.name] = _value_for(column, _SECRET_MARKER)
        elif column.primary_key and isinstance(column.type, String):
            row[column.name] = str(uuid.uuid4())
        elif column.nullable or column.default is not None or column.server_default is not None:
            continue
        elif column.foreign_keys:
            raise AssertionError(
                f"{table.name}.{column.name} needs a parent row other than the "
                "user — extend _seed_row"
            )
        else:
            row[column.name] = _value_for(column, marker)
    return row


async def _register(client: AsyncClient, email: str) -> tuple[str, dict]:
    response = await client.post(
        "/api/v1/auth/register",
        json={"name": "Rider", "email": email, "password": _PASSWORD},
    )
    assert response.status_code == 200, response.text
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    me = await client.get("/api/v1/users/me", headers=headers)
    return me.json()["id"], headers


async def _seed_every_user_table(user_id: str) -> None:
    async with TestSessionLocal() as db:
        # One-to-one tables (unique owner column) take only one row; a seeded
        # row is all the test needs from any of them.
        for table in account_export.user_tables():
            await db.execute(table.insert().values(**_seed_row(table, user_id)))
        await db.commit()


async def _rows_owned_by(user_id: str) -> dict[str, int]:
    counts = {}
    async with TestSessionLocal() as db:
        for table in account_export.user_tables():
            owner = account_export.owner_column(table)
            count = await db.scalar(
                select(func.count()).select_from(table).where(owner == user_id)
            )
            if count:
                counts[table.name] = count
    return counts


# ---------------------------------------------------------------------------
# The schema itself
# ---------------------------------------------------------------------------


def test_every_user_id_column_is_a_foreign_key_to_users():
    """A ``user_id`` without a foreign key is invisible to everything below."""
    unlinked = [
        f"{table.name}.{column.name}"
        for table in Base.metadata.sorted_tables
        for column in table.columns
        if column.name == "user_id" and not column.foreign_keys
    ]
    assert unlinked == []


def test_every_user_table_is_reached_by_a_delete_cascade():
    """The structural half: fails at import time, before any row is written."""
    cascaded = {
        rel.mapper.local_table.name
        for rel in inspect(models.User).relationships
        if "delete" in rel.cascade
    }
    missing = [t.name for t in account_export.user_tables() if t.name not in cascaded]
    assert missing == []


def test_the_walk_actually_finds_tables():
    """Anti-vacuity: an empty walk would make every sweep here pass."""
    names = {t.name for t in account_export.user_tables()}
    assert len(names) >= 30
    assert {"chat_messages", "llm_calls", "plan_day_history", "strava_tokens"} <= names


# ---------------------------------------------------------------------------
# Deletion (Art. 17)
# ---------------------------------------------------------------------------


async def test_deleting_the_account_leaves_no_row_in_any_table(
    client: AsyncClient, foreign_keys_enforced
):
    user_id, headers = await _register(client, "leaver@example.com")
    await _seed_every_user_table(user_id)
    seeded = await _rows_owned_by(user_id)
    assert set(seeded) == {t.name for t in account_export.user_tables()}

    response = await client.request(
        "DELETE", "/api/v1/users/me", headers=headers, json={"password": _PASSWORD}
    )

    assert response.status_code == 200, response.text
    assert await _rows_owned_by(user_id) == {}
    async with TestSessionLocal() as db:
        assert await db.get(models.User, user_id) is None


async def test_deleting_one_account_leaves_every_other_account_alone(
    client: AsyncClient, foreign_keys_enforced
):
    leaver_id, leaver = await _register(client, "leaver@example.com")
    stayer_id, _ = await _register(client, "stayer@example.com")
    await _seed_every_user_table(leaver_id)
    await _seed_every_user_table(stayer_id)
    before = await _rows_owned_by(stayer_id)

    response = await client.request(
        "DELETE", "/api/v1/users/me", headers=leaver, json={"password": _PASSWORD}
    )

    assert response.status_code == 200, response.text
    assert await _rows_owned_by(stayer_id) == before


# ---------------------------------------------------------------------------
# Export (Art. 15 and 20)
# ---------------------------------------------------------------------------


async def test_the_export_holds_every_user_table(client: AsyncClient):
    user_id, headers = await _register(client, "reader@example.com")
    await _seed_every_user_table(user_id)

    response = await client.get("/api/v1/users/me/export", headers=headers)

    assert response.status_code == 200, response.text
    assert "attachment" in response.headers["content-disposition"]
    body = response.json()
    assert body["format"] == account_export.EXPORT_FORMAT
    assert body["version"] == account_export.EXPORT_VERSION
    assert body["account"]["id"] == user_id
    assert body["account"]["email"] == "reader@example.com"
    for table in account_export.user_tables():
        rows = body["tables"][table.name]
        assert len(rows) == 1, table.name
        owner = account_export.owner_column(table)
        assert rows[0][owner.name] == user_id


async def test_the_export_carries_no_credential(client: AsyncClient):
    user_id, headers = await _register(client, "reader@example.com")
    await _seed_every_user_table(user_id)
    async with TestSessionLocal() as db:
        user = await db.get(models.User, user_id)
        user.user_openai_api_key = _SECRET_MARKER
        user.totp_secret = _SECRET_MARKER
        hashed_password = user.hashed_password
        await db.commit()

    response = await client.get("/api/v1/users/me/export", headers=headers)

    assert response.status_code == 200, response.text
    assert _SECRET_MARKER not in response.text
    assert hashed_password not in response.text
    body = response.json()
    for table in [models.User.__table__, *account_export.user_tables()]:
        exported = (
            [body["account"]] if table.name == "users" else body["tables"][table.name]
        )
        for column in table.columns:
            if account_export.is_secret(table, column):
                assert all(column.name not in row for row in exported), (
                    f"{table.name}.{column.name}"
                )


def test_every_encrypted_column_counts_as_a_secret():
    encrypted = [
        (t.name, c.name)
        for t in Base.metadata.sorted_tables
        for c in t.columns
        if isinstance(c.type, models.EncryptedString)
    ]
    assert encrypted, "anti-vacuity: the schema has encrypted columns"
    assert all(
        account_export.is_secret(Base.metadata.tables[t], Base.metadata.tables[t].c[c])
        for t, c in encrypted
    )


async def test_the_export_holds_nobody_elses_rows(client: AsyncClient):
    reader_id, headers = await _register(client, "reader@example.com")
    other_id, _ = await _register(client, "other@example.com")
    await _seed_every_user_table(other_id)

    response = await client.get("/api/v1/users/me/export", headers=headers)

    assert response.status_code == 200, response.text
    assert other_id not in response.text
    assert "other@example.com" not in response.text
    assert response.json()["account"]["id"] == reader_id


async def test_the_export_needs_a_session(client: AsyncClient):
    response = await client.get("/api/v1/users/me/export")
    assert response.status_code == 401
