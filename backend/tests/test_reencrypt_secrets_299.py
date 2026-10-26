"""Moving stored secrets onto their own keys, so the shared one can be retired
(ai-trainer-ops#299). Against the test database, with keys set per test.
"""

from __future__ import annotations

import asyncio

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import Text, select, type_coerce

import crud
import models
from config import _DEDICATED_KEY_FIELDS, SecretPurpose, settings
from database import async_session_maker
from scripts import reencrypt_secrets, verify_restore

SECRETS = {
    ("users", "totp_secret"): "TOTPSECRET234567",
    ("users", "user_openai_api_key"): "sk-openai-not-real",
    ("users", "user_gemini_api_key"): "gemini-not-real",
    ("strava_tokens", "access_token"): "strava-access-not-real",
    ("strava_tokens", "refresh_token"): "strava-refresh-not-real",
    ("intervals_tokens", "api_key"): "intervals-not-real",
}


def _key() -> str:
    return Fernet.generate_key().decode()


def _configure(monkeypatch, *, shared: str = "", **own: str) -> None:
    """The shared key and the dedicated ones named, the rest unset."""
    monkeypatch.setattr(settings, "secrets_encryption_key", shared)
    monkeypatch.setattr(settings, "strava_encryption_key", "")
    for purpose, field in _DEDICATED_KEY_FIELDS.items():
        monkeypatch.setattr(settings, field, own.get(purpose, ""))


async def _store_athlete(email: str) -> str:
    """One athlete with every kind of secret, written under the keys set now."""
    async with async_session_maker() as session:
        user = await crud.create_user(session, email=email, name="Rider", hashed_password="x")
        user.totp_secret = SECRETS[("users", "totp_secret")]
        user.user_openai_api_key = SECRETS[("users", "user_openai_api_key")]
        user.user_gemini_api_key = SECRETS[("users", "user_gemini_api_key")]
        session.add(
            models.StravaToken(
                user_id=user.id,
                access_token=SECRETS[("strava_tokens", "access_token")],
                refresh_token=SECRETS[("strava_tokens", "refresh_token")],
                expires_at=0,
                athlete_id=1,
            )
        )
        session.add(models.IntervalsToken(user_id=user.id, api_key=SECRETS[("intervals_tokens", "api_key")]))
        await session.commit()
        return user.id


async def _raw(table: str, column: str, user_id: str) -> str:
    """The stored value as it is, past the column's decryption."""
    t = models.Base.metadata.tables[table]
    owner = t.c.id if table == "users" else t.c.user_id
    async with async_session_maker() as session:
        return await session.scalar(select(type_coerce(t.c[column], Text)).where(owner == user_id))


def _counts(counts) -> dict[str, reencrypt_secrets.Count]:
    return {count.column: count for count in counts}


async def test_a_dry_run_counts_what_would_move_and_writes_nothing(monkeypatch):
    shared, totp = _key(), _key()
    _configure(monkeypatch, shared=shared)
    user_id = await _store_athlete("dry-299@example.com")
    before = await _raw("users", "totp_secret", user_id)
    _configure(monkeypatch, shared=shared, totp=totp)

    counts = _counts(await reencrypt_secrets.run(commit=False))

    assert counts["users.totp_secret"].moved == 1
    assert await _raw("users", "totp_secret", user_id) == before
    # A purpose without a key of its own has nowhere to move to.
    assert counts["strava_tokens.access_token"].left_alone == "no key of its own"
    assert counts["intervals_tokens.api_key"].left_alone == "no key of its own"


async def test_a_commit_moves_every_secret_onto_its_own_key(monkeypatch):
    shared = _key()
    own = {purpose: _key() for purpose in SecretPurpose.ALL}
    _configure(monkeypatch, shared=shared)
    user_id = await _store_athlete("commit-299@example.com")
    _configure(monkeypatch, shared=shared, **own)

    counts = _counts(await reencrypt_secrets.run(commit=True))

    purposes = {f"{t}.{c}": p for t, c, p in verify_restore.encrypted_columns()}
    for (table, column), secret in SECRETS.items():
        assert counts[f"{table}.{column}"].moved == 1
        raw = await _raw(table, column, user_id)
        # Encrypted once, under its own key alone: not twice, not under the shared one.
        assert Fernet(own[purposes[f"{table}.{column}"]].encode()).decrypt(raw.encode()) == secret.encode()

    # The shared key can go now: with the dedicated keys alone the app reads them.
    _configure(monkeypatch, **own)
    async with async_session_maker() as session:
        user = await session.get(models.User, user_id)
        assert user.totp_secret == SECRETS[("users", "totp_secret")]
        token = await session.get(models.IntervalsToken, user_id)
        assert token.api_key == SECRETS[("intervals_tokens", "api_key")]
    # And a second pass finds nothing left to move.
    again = _counts(await reencrypt_secrets.run(commit=False))
    assert again["users.totp_secret"].moved == 0
    assert again["users.totp_secret"].on_own_key == 1


async def test_a_value_that_was_never_encrypted_is_left_as_it_is(monkeypatch):
    _configure(monkeypatch)
    user_id = await _store_athlete("plain-299@example.com")
    _configure(monkeypatch, shared=_key(), totp=_key())

    counts = _counts(await reencrypt_secrets.run(commit=True))

    assert counts["users.totp_secret"].plaintext == 1
    assert counts["users.totp_secret"].moved == 0
    assert await _raw("users", "totp_secret", user_id) == SECRETS[("users", "totp_secret")]


async def test_nothing_is_written_while_any_value_opens_with_no_key(monkeypatch):
    shared, totp = _key(), _key()
    _configure(monkeypatch, shared=_key())
    await _store_athlete("orphan-299@example.com")
    _configure(monkeypatch, shared=shared)
    movable = await _store_athlete("movable-299@example.com")
    before = await _raw("users", "totp_secret", movable)
    _configure(monkeypatch, shared=shared, totp=totp)

    with pytest.raises(reencrypt_secrets.Unreadable):
        await reencrypt_secrets.run(commit=True)

    # The movable row was rolled back with the rest.
    assert await _raw("users", "totp_secret", movable) == before


async def test_a_value_no_key_opens_stops_the_run_before_any_write(monkeypatch):
    """Review of #1147: the updates used to be issued, and their rows locked,
    before the run found the unreadable value and rolled back."""
    shared = _key()
    _configure(monkeypatch, shared=shared)
    await _store_athlete("movable-first-299@example.com")
    _configure(monkeypatch, shared=_key())
    await _store_athlete("orphan-later-299@example.com")
    _configure(monkeypatch, shared=shared, totp=_key())
    writes: list = []

    async def spy(*args):
        writes.append(args)
        return True

    monkeypatch.setattr(reencrypt_secrets, "_move", spy)
    with pytest.raises(reencrypt_secrets.Unreadable):
        await reencrypt_secrets.run(commit=True)

    assert writes == []


async def test_a_row_the_app_rewrote_meanwhile_is_not_overwritten(monkeypatch):
    shared, totp = _key(), _key()
    _configure(monkeypatch, shared=shared, totp=totp)
    user_id = await _store_athlete("meanwhile-299@example.com")
    current = await _raw("users", "totp_secret", user_id)
    table = models.User.__table__

    async with async_session_maker() as session:
        wrote = await reencrypt_secrets._move(
            session, table, table.c.totp_secret, [user_id], "the value read before", "a rotated token"
        )
        await session.commit()

    assert wrote is False
    assert await _raw("users", "totp_secret", user_id) == current


def test_a_dry_run_from_the_command_line_says_what_would_move(monkeypatch, capsys):
    shared, totp = _key(), _key()
    _configure(monkeypatch, shared=shared, totp=totp)

    assert reencrypt_secrets.main([]) == 0
    out = capsys.readouterr().out
    assert "dry run, nothing is written" in out
    assert "users.totp_secret" in out
    assert "would move. Run again with --commit" in out


def test_the_command_line_refuses_to_commit_past_a_value_no_key_opens(monkeypatch, capsys):
    shared = _key()
    _configure(monkeypatch, shared=_key())
    asyncio.run(_store_athlete("orphan-cli-299@example.com"))
    _configure(monkeypatch, shared=shared, totp=_key())

    assert reencrypt_secrets.main(["--commit"]) == 1
    assert "Nothing was written" in capsys.readouterr().err
