"""Password hashing: Argon2id, with legacy bcrypt hashes still accepted (#329).

Pure: no database and no I/O. auth signs in with it, and the Authelia user store
writes its hashes with it, so it sits below both (ai-trainer-ops#304).
"""

import secrets

import bcrypt
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error

_argon2_hasher = PasswordHasher()


def hash_password(plain: str) -> str:
    """Hash a password with Argon2id.

    Argon2 (unlike bcrypt) has no 72-byte input limit, so long passphrases are
    hashed in full. Legacy bcrypt hashes are still accepted by
    ``verify_password`` and upgraded on successful login (see
    ``password_needs_rehash``). See #329.
    """
    return _argon2_hasher.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    if hashed.startswith("$argon2"):
        try:
            return _argon2_hasher.verify(hashed, plain)
        except Argon2Error:
            return False

    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


_absent_user_hash: str | None = None


def _hash_nobody_matches() -> str:
    """An Argon2 hash no submitted password can match, built like a real one.

    Produced by ``hash_password`` rather than written down as a constant, so its
    cost parameters cannot drift away from the hashes it stands in for. A
    stand-in cheaper than the real thing reopens the gap it exists to close, and
    a hard-coded hash would stop following ``PasswordHasher``'s defaults the next
    time argon2-cffi raises them.

    Computed on first use rather than at import: a process that never sees a
    login attempt for a missing account should not pay an Argon2 hash for it,
    and paying at import would slow every test collection for nothing.
    """
    global _absent_user_hash
    if _absent_user_hash is None:
        _absent_user_hash = hash_password(secrets.token_urlsafe(32))
    return _absent_user_hash


def password_matches(password: str, hashed: str | None) -> bool:
    """Whether *password* matches *hashed* — at one cost, hash or no hash.

    ``None`` means "no such account, or no usable password on it", and is a legal
    argument on purpose. The callers used to decide that themselves, each writing
    ``user is None or not verify_password(...)``, which skipped the Argon2
    verification entirely when the account did not exist. Argon2 is deliberately
    expensive, so that short-circuit was a plain enumeration oracle: measured on
    ``/auth/login``, a wrong password for an existing account answered in 165 ms
    and one for an address with no account in 8 ms — 20x, from a single request,
    no averaging needed (ai-trainer-ops#35).

    So the absent case spends a verification it knows will fail, and that
    decision lives in one function rather than at each call site, where the next
    caller would write the short-circuit again.

    Not constant-time in the strict sense, and not trying to be: a legacy bcrypt
    hash (#329) verifies on a different curve from Argon2, so a tell remains
    between two kinds of *existing* account — which answers nothing an attacker
    wants to know. What had to go is the tell between existing and not.
    """
    if hashed is None:
        hashed = _hash_nobody_matches()
    return verify_password(password, hashed)


def password_needs_rehash(hashed: str) -> bool:
    """Whether a stored hash should be replaced after a successful verify.

    True for any non-Argon2 (legacy bcrypt) hash, or an Argon2 hash made with
    out-of-date parameters, so callers can transparently upgrade it on login.
    """
    if not hashed.startswith("$argon2"):
        return True
    try:
        return _argon2_hasher.check_needs_rehash(hashed)
    except Argon2Error:
        return False
