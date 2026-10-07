"""One key per secret type, and the migration that lands it (ai-trainer-ops#12).

`SECRETS_ENCRYPTION_KEY` encrypted every secret this app holds on an athlete's
behalf, so rotating it meant re-encrypting all of them in one operation — and
the reason to rotate is usually a suspicion, which is the worst moment for an
all-or-nothing change to someone else's credentials.

There are **four** purposes and not the three the backlog named: `totp_secret`
became an encrypted column with the second factor (#688), after that note was
written. Rotating the shared key today would also lock every athlete out of
their authenticator. The sweep at the bottom is what keeps the count honest: it
reads the model definitions, so a fifth encrypted column cannot arrive without
someone deciding which key it belongs to.

What this suite has to prove, beyond "each purpose has a key":

- a purpose's ciphertext does not open with another purpose's key, and the
  separation survives SQLAlchemy's statement cache — which keys off the type's
  constructor arguments, so it holds only because the attribute is named the
  same as the parameter;
- a row written before a dedicated key was configured stays readable after, and
  moves to the new key when it is next written. That is the whole migration
  path, and without it the split would need a four-table re-encryption up front;
- the shared key still being required is not an oversight. Old ciphertext is
  only readable with it, so it is retired by a re-encryption pass, not by a
  configuration change.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select, text

import crud
import models
from config import SecretPurpose, Settings, settings
from database import async_session_maker

BACKEND = Path(__file__).resolve().parents[1]


def _key() -> str:
    return Fernet.generate_key().decode()


def _opens(ciphertext: str, key: str) -> bool:
    """Whether *key* alone decrypts *ciphertext*."""
    try:
        Fernet(key.encode()).decrypt(ciphertext.encode())
    except InvalidToken:
        return False
    return True


async def _stored(column: str, table: str, where: str, value: str) -> str:
    """What is actually on disk, read around the ORM so the type cannot help."""
    async with async_session_maker() as session:
        result = await session.execute(
            text(f"SELECT {column} FROM {table} WHERE {where} = :value"),
            {"value": value},
        )
        return result.scalar_one()


@pytest.fixture
def keys(monkeypatch) -> dict[str, str]:
    """A distinct key for the shared slot and for each purpose."""
    issued = {"shared": _key(), **{purpose: _key() for purpose in SecretPurpose.ALL}}
    monkeypatch.setattr(settings, "secrets_encryption_key", issued["shared"])
    monkeypatch.setattr(settings, "strava_encryption_key", "")
    monkeypatch.setattr(settings, "strava_token_encryption_key", issued["strava"])
    monkeypatch.setattr(settings, "intervals_encryption_key", issued["intervals"])
    monkeypatch.setattr(settings, "ai_key_encryption_key", issued["ai_keys"])
    monkeypatch.setattr(settings, "totp_encryption_key", issued["totp"])
    return issued


async def _write_one_of_everything(email: str) -> str:
    """An athlete with a secret of every purpose. Returns the user id."""
    async with async_session_maker() as session:
        user = await crud.create_user(
            session, email=email, name="Rider", hashed_password="x"
        )
        user.totp_secret = "TOTPSECRET234567"
        user.user_openai_api_key = "sk-openai-not-real"
        session.add(
            models.StravaToken(
                user_id=user.id,
                access_token="strava-access-not-real",
                refresh_token="strava-refresh-not-real",
                expires_at=1,
                athlete_id=1,
            )
        )
        session.add(
            models.IntervalsToken(user_id=user.id, api_key="intervals-not-real")
        )
        await session.commit()
        return user.id


# ---------------------------------------------------------------------------
# Separation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_each_purpose_is_encrypted_with_its_own_key(keys):
    """The point of the whole change, stated as ciphertext.

    Checked by reading the column with raw SQL and trying the keys by hand,
    rather than by round-tripping through the ORM: a round trip is green as long
    as writing and reading agree, which they would even if all four purposes
    shared one key.
    """
    user_id = await _write_one_of_everything("split@example.com")

    on_disk = {
        "totp": await _stored("totp_secret", "users", "id", user_id),
        "ai_keys": await _stored("user_openai_api_key", "users", "id", user_id),
        "strava": await _stored("access_token", "strava_tokens", "user_id", user_id),
        "intervals": await _stored("api_key", "intervals_tokens", "user_id", user_id),
    }

    for purpose, ciphertext in on_disk.items():
        assert ciphertext.startswith("gAAAAA"), f"{purpose} is not a Fernet token"
        assert _opens(ciphertext, keys[purpose]), (
            f"{purpose} does not open with its own key"
        )
        assert not _opens(ciphertext, keys["shared"]), (
            f"{purpose} still opens with the shared key, so nothing was split"
        )
        for other in SecretPurpose.ALL:
            if other == purpose:
                continue
            assert not _opens(ciphertext, keys[other]), (
                f"a {purpose} secret opens with the {other} key — the purposes "
                "are not separated, and one leaked key costs two secret types"
            )


@pytest.mark.asyncio
async def test_every_purpose_still_round_trips(keys):
    """Separation is worthless if the app can no longer read its own secrets."""
    user_id = await _write_one_of_everything("roundtrip@example.com")

    async with async_session_maker() as session:
        user = await crud.get_user_by_id(session, user_id)
        strava = (
            await session.execute(
                select(models.StravaToken).where(
                    models.StravaToken.user_id == user_id
                )
            )
        ).scalar_one()
        intervals = (
            await session.execute(
                select(models.IntervalsToken).where(
                    models.IntervalsToken.user_id == user_id
                )
            )
        ).scalar_one()

    assert user.totp_secret == "TOTPSECRET234567"
    assert user.user_openai_api_key == "sk-openai-not-real"
    assert strava.access_token == "strava-access-not-real"
    assert strava.refresh_token == "strava-refresh-not-real"
    assert intervals.api_key == "intervals-not-real"


def test_the_statement_cache_distinguishes_two_purposes():
    """Why ``purpose`` is named ``purpose`` and not anything else.

    SQLAlchemy builds a cacheable type's key from its constructor arguments, by
    looking up each parameter name as an attribute. A compiled statement carries
    its bind processor, so two columns whose types compared as equal would share
    a cache entry — and one of them would be encrypted with the other's key.

    It works by a naming coincidence between the parameter and the attribute it
    sets, which is exactly the kind of dependency that survives a refactor only
    if something fails when it breaks.
    """
    strava = models.EncryptedString(SecretPurpose.STRAVA)
    totp = models.EncryptedString(SecretPurpose.TOTP)

    assert strava._static_cache_key != totp._static_cache_key
    assert ("purpose", SecretPurpose.STRAVA) in strava._static_cache_key
    assert models.EncryptedString.cache_ok is True, (
        "turning caching off would hide the problem rather than fix it, and cost "
        "every query that touches an encrypted column"
    )


# ---------------------------------------------------------------------------
# The migration path: one secret type at a time, no re-encryption up front
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_row_written_before_the_split_stays_readable_after_it(monkeypatch):
    """The reason this can be deployed at all.

    An operator adds one dedicated key. Everything already in the database was
    written under the shared one, and must keep working — otherwise the split is
    a migration that has to re-encrypt four tables before anyone can log in,
    which is the kind of change that does not get made.
    """
    shared = _key()
    monkeypatch.setattr(settings, "secrets_encryption_key", shared)
    monkeypatch.setattr(settings, "strava_encryption_key", "")
    monkeypatch.setattr(settings, "totp_encryption_key", "")

    async with async_session_maker() as session:
        user = await crud.create_user(
            session, email="before@example.com", name="B", hashed_password="x"
        )
        user.totp_secret = "WRITTEN-BEFORE-234"
        await session.commit()
        user_id = user.id

    monkeypatch.setattr(settings, "totp_encryption_key", _key())

    async with async_session_maker() as session:
        assert (
            await crud.get_user_by_id(session, user_id)
        ).totp_secret == "WRITTEN-BEFORE-234"


@pytest.mark.asyncio
async def test_rewriting_a_row_moves_it_to_the_dedicated_key(monkeypatch):
    """Which is what makes the shared key retireable, eventually.

    No migration script re-encrypts anything, so a purpose moves across as its
    rows are next written. That is complete for Strava (every refresh rewrites
    the token) and effectively never for a TOTP secret, which is written once at
    enrollment. So the shared key cannot be dropped on a timer — see the test
    below for why it is still required.
    """
    shared = _key()
    monkeypatch.setattr(settings, "secrets_encryption_key", shared)
    monkeypatch.setattr(settings, "strava_encryption_key", "")
    monkeypatch.setattr(settings, "totp_encryption_key", "")

    async with async_session_maker() as session:
        user = await crud.create_user(
            session, email="moves@example.com", name="M", hashed_password="x"
        )
        user.totp_secret = "WRITTEN-BEFORE-234"
        await session.commit()
        user_id = user.id

    assert _opens(await _stored("totp_secret", "users", "id", user_id), shared)

    dedicated = _key()
    monkeypatch.setattr(settings, "totp_encryption_key", dedicated)
    async with async_session_maker() as session:
        user = await crud.get_user_by_id(session, user_id)
        user.totp_secret = "REWRITTEN-AFTER-99"
        await session.commit()

    after = await _stored("totp_secret", "users", "id", user_id)
    assert _opens(after, dedicated)
    assert not _opens(after, shared), (
        "a rewritten row still opens with the shared key, so nothing ever moves "
        "off it and the split buys no independence"
    )


@pytest.mark.asyncio
async def test_a_purpose_with_no_dedicated_key_keeps_using_the_shared_one(
    monkeypatch,
):
    """Which is what "one secret type at a time" means, concretely.

    Setting the TOTP key must not disturb Strava. If adding one key required
    adding all four, the split would be the same all-or-nothing operation it
    exists to end.
    """
    shared = _key()
    totp_only = _key()
    monkeypatch.setattr(settings, "secrets_encryption_key", shared)
    monkeypatch.setattr(settings, "strava_encryption_key", "")
    monkeypatch.setattr(settings, "totp_encryption_key", totp_only)
    monkeypatch.setattr(settings, "strava_token_encryption_key", "")
    monkeypatch.setattr(settings, "intervals_encryption_key", "")
    monkeypatch.setattr(settings, "ai_key_encryption_key", "")

    user_id = await _write_one_of_everything("partial@example.com")

    assert _opens(await _stored("totp_secret", "users", "id", user_id), totp_only)
    assert _opens(
        await _stored("access_token", "strava_tokens", "user_id", user_id), shared
    )
    assert _opens(
        await _stored("api_key", "intervals_tokens", "user_id", user_id), shared
    )


def test_a_dedicated_key_is_tried_before_the_shared_one() -> None:
    """Order is the mechanism, not a detail.

    ``MultiFernet`` encrypts with the first key and decrypts with any, so the
    shared key coming second is what makes a new write land on the dedicated key
    while old rows still open. Reversed, every write would stay on the shared
    key and nothing would ever move.
    """
    shared, dedicated = _key(), _key()
    configured = Settings(
        app_env="production",
        secrets_encryption_key=shared,
        totp_encryption_key=dedicated,
    )

    assert configured.encryption_keys_for(SecretPurpose.TOTP) == [dedicated, shared]
    assert configured.encryption_keys_for(SecretPurpose.STRAVA) == [shared]


# ---------------------------------------------------------------------------
# What the configuration refuses
# ---------------------------------------------------------------------------


def test_the_shared_key_is_still_required_in_production() -> None:
    """Not an oversight, and the reason is worth keeping written down.

    Every secret stored before the split is only readable with it, and nothing
    re-encrypts them — a TOTP secret is written once at enrollment and never
    again. Dropping the shared key would therefore lock athletes out of their
    authenticator, which is the failure this issue is about, caused by the fix
    for it. It is retired by a re-encryption pass, not by a deploy.
    """
    with pytest.raises(ValueError, match="SECRETS_ENCRYPTION_KEY"):
        Settings(
            app_env="production",
            secrets_encryption_key="",
            strava_encryption_key="",
            strava_token_encryption_key=_key(),
            intervals_encryption_key=_key(),
            ai_key_encryption_key=_key(),
            totp_encryption_key=_key(),
        )


@pytest.mark.parametrize(
    "field,name",
    [
        ("strava_token_encryption_key", "STRAVA_TOKEN_ENCRYPTION_KEY"),
        ("intervals_encryption_key", "INTERVALS_ENCRYPTION_KEY"),
        ("ai_key_encryption_key", "AI_KEY_ENCRYPTION_KEY"),
        ("totp_encryption_key", "TOTP_ENCRYPTION_KEY"),
    ],
)
def test_a_dedicated_key_fernet_cannot_use_is_refused_at_boot(
    field: str, name: str
) -> None:
    """And refused *by name*, which is the half that matters at 3am.

    40 characters decoding to 30 bytes is what ``token_urlsafe(30)`` and
    ``openssl rand -base64 30`` produce — plausible, and not a Fernet key. Left
    unchecked it would fail on first use, on one column family, in a process
    that booted cleanly while the other three purposes kept working.
    """
    with pytest.raises(ValueError, match=name):
        Settings(
            app_env="production",
            secrets_encryption_key=_key(),
            **{field: "x" * 40},
        )


@pytest.mark.parametrize("purpose", SecretPurpose.ALL)
def test_copying_the_shared_key_into_a_dedicated_one_is_refused(purpose: str) -> None:
    """The obvious wrong move, and the one that reads as done.

    It validates, encrypts, decrypts, and shows a configured key on the
    dashboard, while rotating either value still takes every secret type with
    it. Failing at boot is the only moment anyone finds out before the incident
    they are rotating for.
    """
    shared = _key()
    field = {
        SecretPurpose.STRAVA: "strava_token_encryption_key",
        SecretPurpose.INTERVALS: "intervals_encryption_key",
        SecretPurpose.AI_KEYS: "ai_key_encryption_key",
        SecretPurpose.TOTP: "totp_encryption_key",
    }[purpose]

    with pytest.raises(ValueError, match="same value as SECRETS_ENCRYPTION_KEY"):
        Settings(app_env="production", secrets_encryption_key=shared, **{field: shared})


def test_the_legacy_strava_name_is_still_the_shared_key() -> None:
    """The trap this split had to avoid, pinned so a later tidy-up cannot spring it.

    ``STRAVA_ENCRYPTION_KEY`` is the pre-#613 alias for the *shared* key, which
    is why the dedicated one is ``STRAVA_TOKEN_ENCRYPTION_KEY``. Had the backlog's
    proposed name been used, a deployment still setting the old variable would
    have kept Strava working and sent intervals, AI and TOTP secrets to a
    ``SECRETS_ENCRYPTION_KEY`` it never set — plaintext, silently. That is #612,
    reintroduced by a rename.
    """
    legacy = _key()
    configured = Settings(
        app_env="production", secrets_encryption_key="", strava_encryption_key=legacy
    )

    for purpose in SecretPurpose.ALL:
        assert configured.encryption_keys_for(purpose) == [legacy], (
            f"{purpose} no longer reads the legacy shared key, so a deployment "
            "that still sets only STRAVA_ENCRYPTION_KEY stores it as plaintext"
        )


# ---------------------------------------------------------------------------
# Keeping the count of purposes honest
# ---------------------------------------------------------------------------


def _encrypted_columns() -> dict[str, str]:
    """Every ``EncryptedString`` column in ``models.py``, mapped to its purpose.

    Read from the source because the question is whether a *new* column was
    given a purpose by a human, and an attribute lookup on the live type would
    answer a different question — ``EncryptedString`` cannot be constructed
    without one, so anything that imported is already past the check.
    """
    tree = ast.parse((BACKEND / "models.py").read_text())
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
            continue
        for call in ast.walk(node):
            if not (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "EncryptedString"
            ):
                continue
            argument = call.args[0] if call.args else None
            if isinstance(argument, ast.Attribute):
                found[node.target.id] = argument.attr
            else:  # pragma: no cover - a literal would be a review finding
                found[node.target.id] = ast.unparse(argument)
    return found


def test_the_encrypted_columns_are_the_ones_this_suite_knows_about() -> None:
    """A fifth encrypted column must not quietly inherit the shared key.

    This is how the backlog's count came to be wrong: ``totp_secret`` was added
    with #688 and the note saying "three secret types" was never revisited, so
    the all-or-nothing rotation silently grew to include every athlete's second
    factor. The next column gets noticed here instead.
    """
    assert _encrypted_columns() == {
        "user_openai_api_key": "AI_KEYS",
        "user_gemini_api_key": "AI_KEYS",
        "totp_secret": "TOTP",
        "access_token": "STRAVA",
        "refresh_token": "STRAVA",
        "api_key": "INTERVALS",
    }


def test_every_purpose_has_a_column_and_a_setting() -> None:
    """Neither list may grow without the other.

    A purpose with no column is dead configuration an operator can set and
    wonder about; a column with no setting cannot happen, because the type needs
    one — but a purpose missing from ``ALL`` would drop out of the sweeps above
    and stop being checked at all.
    """
    in_use = set(_encrypted_columns().values())
    declared = {purpose.upper() for purpose in SecretPurpose.ALL}

    assert in_use == declared, f"columns use {in_use}, SecretPurpose declares {declared}"

    for purpose in SecretPurpose.ALL:
        assert Settings(
            app_env="development"
        ).encryption_keys_for(purpose) == [], "a dev default should need no key"
