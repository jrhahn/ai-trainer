"""Authelia's file-based ``users_database.yml`` — the one module that writes it.

Authelia is the *user store* in this deployment, not a forward-auth proxy: its
middlewares are attached to no router and the app reads this file directly
(#324, #696). With ``AUTHELIA_AUTH_ENABLED`` the password lives here and the
``users.hashed_password`` column holds a random throwaway, so this file — not
the database — is what decides whether somebody can log in.

Why it is a service and not three functions in ``auth_router`` (ai-trainer-ops#35)
----------------------------------------------------------------------------------
Because it was, and that is why account deletion did not delete anything. Two
routes delete a user — ``DELETE /users/me`` and ``DELETE /admin/users/{id}`` —
and neither may import ``auth_router``, so neither could reach the only code
that knew this file's shape. Both dropped the database row and left the
credential on disk, and ``POST /auth/login`` recreates a missing row from a
valid credential, so the next login brought the account back with a fresh token.
Measured, not reasoned about: delete, log in with the same password, 200 and a
new JWT.

So the knowledge lives in one importable place. Splitting it instead — a
``delete`` next to the callers, a ``create`` in the router — would have meant two
implementations of the careful part below, and #684 is what that costs.

Writing it safely
-----------------
Every write takes an exclusive ``flock`` on a *separate* lock file and lands via
``os.replace``, so Authelia's file-watcher only ever sees a complete YAML
document and a single CREATE event, never a truncate-then-write it could cache
as an empty user list.
"""

from __future__ import annotations

import contextlib
import fcntl
import logging
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

import yaml

import auth

logger = logging.getLogger(__name__)


class StoreMissing(RuntimeError):
    """The configured ``users_database.yml`` is not there."""


class StoreUnreadable(RuntimeError):
    """The file is there and this process cannot read or write it.

    Not a credential problem and must never be reported as one. It happened
    twice (#684, #696): the file is shared with the Authelia container, whose
    entrypoint chowns it to root, while this process runs as uid 1001. The
    boot-time check in ``auth`` cannot catch it either, because the condition
    appears whenever that other container restarts — long after boot.
    """


class EmailTaken(ValueError):
    """The address already has an entry."""


def _store_path() -> Path:
    """Where the store is, read through ``auth`` rather than ``settings``.

    ``auth`` is where the whole app reads this setting from, which makes it the
    one place a test or an operator override has to land.
    """
    return Path(auth.AUTHELIA_USERS_DB_PATH)


def _carry_over_file_identity(original: Path, replacement: str) -> None:
    """Give *replacement* the mode and ownership that *original* has.

    ``os.replace`` swaps in a new inode, so without this an atomic write
    silently re-owns the file to whoever ran it and resets the mode to
    ``mkstemp``'s 0600. That is how #684 happened: a registration handled while
    the backend still ran as root rewrote the store as ``root:root 0600``, and
    the next deploy — which moved the backend to uid 1001 — could no longer read
    it. Login and registration both read this file, so the whole auth surface
    went down until the ownership was restored by hand.

    Ownership is best-effort: ``chown`` needs privilege the container
    deliberately no longer has, and failing a registration over it would be
    worse than writing a file the process already owns. The mode is not
    best-effort — a widened mode on a file of password hashes is a real
    regression, and the process always owns the temp file, so the call cannot
    fail for lack of privilege.
    """
    try:
        stat_result = original.stat()
    except FileNotFoundError:
        return
    os.chmod(replacement, stat.S_IMODE(stat_result.st_mode))
    if (os.geteuid(), os.getegid()) != (stat_result.st_uid, stat_result.st_gid):
        with contextlib.suppress(PermissionError, OSError):
            os.chown(replacement, stat_result.st_uid, stat_result.st_gid)


def _write_atomically(db_path: Path, data: dict[str, Any]) -> None:
    """Replace the store with *data* in one inode swap. See the module docstring."""
    tmp_fd, tmp_name = tempfile.mkstemp(
        dir=db_path.parent, suffix=".tmp", prefix="users_database_"
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as tmp_fh:
            yaml.dump(data, tmp_fh, default_flow_style=False, allow_unicode=True)
        _carry_over_file_identity(db_path, tmp_name)
        os.replace(tmp_name, db_path)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)
        raise


@contextlib.contextmanager
def _locked(db_path: Path):
    """Hold the store's lock for one read-modify-write.

    A lock file of its own rather than the store itself, so the file Authelia
    watches is never opened for writing except by ``os.replace``.
    """
    lock_path = db_path.with_suffix(".lock")
    with open(lock_path, "w", encoding="utf-8") as lock_fh:
        fcntl.flock(lock_fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)


def _read_users(db_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """The whole document and its ``users`` mapping."""
    with open(db_path, "r", encoding="utf-8") as fh:
        data: dict[str, Any] = yaml.safe_load(fh) or {}
    return data, data.get("users") or {}


@contextlib.contextmanager
def _as_store_error(while_doing: str):
    """Report an unusable store as :class:`StoreUnreadable`, not as a 500.

    ``yaml.YAMLError`` sits next to ``OSError`` on purpose: a store someone
    hand-edited into invalid YAML is the same operator condition as one this
    process cannot open, and it is a likelier one — editing this file by hand is
    how a user gets added and how a forgotten password gets reset, since neither
    has a route (ai-trainer-ops#35). Without this it surfaced as a bare 500 with
    a traceback as its only explanation, which is what #684 and #696 both looked
    like before they had a 503.

    One place rather than three, because the routes that call in have no way to
    tell these conditions apart and should not have to.
    """
    try:
        yield
    except (OSError, yaml.YAMLError) as exc:
        logger.error(
            "The Authelia user store at %s is unusable (uid=%s) while %s: %s. "
            "Check the ownership of the bind mount and that the file is valid "
            "YAML; nothing that reads it can work until it is.",
            _store_path(),
            os.getuid(),
            while_doing,
            exc,
        )
        raise StoreUnreadable("The user store could not be read.") from exc


def _find_entry(users: dict[str, Any], email: str) -> str | None:
    """The key whose entry is *email*'s, or None.

    Entries are keyed by username, which registration sets to the email — but
    the ``email`` field is checked too, because an operator editing this file by
    hand is a supported way to add a user and need not have used that
    convention.
    """
    for key, entry in users.items():
        if key == email or (entry or {}).get("email") == email:
            return key
    return None


def create_user(email: str, display_name: str, password: str) -> None:
    """Append a new user entry, or raise :class:`EmailTaken`."""
    db_path = _store_path()
    if not db_path.exists():
        raise StoreMissing(f"Authelia users database not found at {db_path}")

    with _as_store_error("creating a user"), _locked(db_path):
        data, users = _read_users(db_path)
        if _find_entry(users, email) is not None:
            raise EmailTaken("Email already registered")

        users[email] = {
            "disabled": False,
            "displayname": display_name,
            "email": email,
            "password": auth.hash_password(password),
            "groups": [],
        }
        data["users"] = users
        _write_atomically(db_path, data)


def delete_user(email: str) -> bool:
    """Remove *email*'s entry; report whether there was one (ai-trainer-ops#35).

    A no-op returning False when header auth is off or no store is configured,
    so the deletion routes can call it unconditionally rather than each deciding
    for itself whether this deployment has a store — which is how one of them
    ends up not calling it.

    An unreadable store raises rather than returning False. Reporting an account
    deleted while its password still works is the one outcome worse than failing
    the request: the athlete has been told it is gone and stops looking.
    """
    if not auth.AUTHELIA_AUTH_ENABLED or not auth.AUTHELIA_USERS_DB_PATH:
        return False

    db_path = _store_path()
    if not db_path.exists():
        raise StoreMissing(f"Authelia users database not found at {db_path}")

    with _as_store_error("deleting a user"), _locked(db_path):
        data, users = _read_users(db_path)
        key = _find_entry(users, email)
        if key is None:
            # Not an error: an account registered before this deployment
            # switched to Authelia has no entry here, and its row is still
            # the thing to delete.
            return False
        del users[key]
        data["users"] = users
        _write_atomically(db_path, data)
    return True


def verify_credentials(email: str, password: str) -> bool:
    """Whether *password* is *email*'s, according to the store.

    Checked against the file rather than through Authelia's ``/api/firstfactor``,
    which is a browser-session API: it requires an existing session cookie and
    rejects server-side requests that have none.
    """
    db_path = _store_path()
    if not db_path.exists():
        return False

    with _as_store_error("verifying credentials"):
        _, users = _read_users(db_path)

    key = _find_entry(users, email)
    entry = users.get(key) if key is not None else None

    # Resolved to a hash or to None, then checked in one place, so that "no such
    # user", "disabled" and "entry without a password" all cost what a wrong
    # password costs. Returning False directly from any of them timed the answer
    # for the attacker (ai-trainer-ops#35); ``disabled`` is in the list because
    # "this address exists but is switched off" is also worth not telling them.
    hashed: str | None = None
    if entry is not None and not entry.get("disabled", False):
        hashed = entry.get("password") or None
    return auth.password_matches(password, hashed)
