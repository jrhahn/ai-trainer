"""Proof-of-work challenge gating public registration (#686).

Why proof-of-work rather than Turnstile or hCaptcha
---------------------------------------------------
Those read behavioural signals and are strictly better at telling a human from
a script. They also require loading a third-party script and letting it talk to
its own host — which means punching ``script-src``/``connect-src`` holes in the
CSP that #677 exists to keep shut, and letting that third party see every
visitor of a self-hosted app. That trade was not worth making for the threat
actually observed here: nine signups over two months, none of which ever logged
in (#686). This runs entirely on this origin and needs no CSP change at all.

Be clear about what it buys. A determined attacker implements the solver and
pays a few milliseconds per account. What it stops is the *naive* bot: one that
POSTs the registration form without executing any JavaScript cannot obtain a
solution at all, because solving requires running the loop. The difficulty
therefore matters far less than the protocol requirement does, which is why the
default is tuned for an imperceptible client delay rather than for a high cost.

The scheme (Altcha-compatible)
------------------------------
1. The server picks a random ``salt`` and a secret ``number``, publishes
   ``challenge = sha256(salt + number)`` along with an HMAC over the parameters.
2. The client finds ``number`` by trying 0, 1, 2, … — brute force is the point.
3. The server accepts when the HMAC is intact, unexpired, unused, and the
   submitted number reproduces the challenge.

The HMAC is what removes the need for server-side state on the *issuing* side:
a challenge is self-describing and cannot be forged, so nothing has to be
remembered between issuing and solving. State is needed only to stop a solved
challenge being submitted twice; see :class:`_ReplayGuard`.

The signing key is derived from ``JWT_SECRET`` rather than being a new setting
of its own. Every secret this app has added lately had to be threaded through
compose, the ansible template and two places in the deploy workflow, and #617
and #684 are both cases where one of those was missed and the result was a
silent fallback rather than an error. Deriving removes that failure mode
entirely, and domain separation via the label keeps it independent of the
signing key for session tokens.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass

from config import settings

_KEY_LABEL = b"ai-trainer/captcha/v1"
_ALGORITHM = "SHA-256"

# Bound on what a client may submit. Without it a caller could hand us a number
# with a million digits and make the verifier do the work the challenge was
# supposed to make *them* do.
_MAX_SUBMITTED_NUMBER = 10_000_000


@dataclass(frozen=True)
class Challenge:
    """One issued challenge, in the shape the client receives it."""

    algorithm: str
    challenge: str
    salt: str
    signature: str
    maxnumber: int

    def as_dict(self) -> dict[str, str | int]:
        return {
            "algorithm": self.algorithm,
            "challenge": self.challenge,
            "salt": self.salt,
            "signature": self.signature,
            "maxnumber": self.maxnumber,
        }


class CaptchaError(Exception):
    """A solution was missing, malformed, expired, replayed or simply wrong."""


def _signing_key() -> bytes:
    """Derive the HMAC key from JWT_SECRET under a fixed label."""
    return hmac.new(
        settings.jwt_secret.encode("utf-8"), _KEY_LABEL, hashlib.sha256
    ).digest()


def _sign(payload: str) -> str:
    return hmac.new(_signing_key(), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _digest(salt: str, number: int) -> str:
    return hashlib.sha256(f"{salt}{number}".encode("utf-8")).hexdigest()


class _ReplayGuard:
    """Remembers spent challenges so a solution cannot be submitted twice.

    Without this the whole scheme collapses to a single puzzle: solve once,
    reuse the answer for every signup. Entries are dropped once they are older
    than the challenge lifetime, since an expired challenge is refused by the
    signature check anyway and remembering it proves nothing.

    In-process, therefore single-replica — the same caveat as
    ``services/rate_limit.py``, recorded in ``docs/multi_replica.md``.
    """

    def __init__(self) -> None:
        self._seen: dict[str, float] = {}

    def claim(self, signature: str, *, now: float) -> bool:
        """Mark *signature* as spent; ``False`` if it already was."""
        self._prune(now)
        if signature in self._seen:
            return False
        self._seen[signature] = now
        return True

    def _prune(self, now: float) -> None:
        ttl = settings.captcha_ttl_seconds
        expired = [key for key, seen in self._seen.items() if now - seen > ttl]
        for key in expired:
            del self._seen[key]

    def reset(self) -> None:
        self._seen.clear()


_replay_guard = _ReplayGuard()


def issue_challenge(*, now: float | None = None) -> Challenge:
    """Create a fresh challenge for a client to solve."""
    issued_at = time.time() if now is None else now
    maxnumber = settings.captcha_max_number
    salt = f"{secrets.token_hex(12)}.{int(issued_at)}"
    number = secrets.randbelow(maxnumber + 1)
    challenge = _digest(salt, number)
    return Challenge(
        algorithm=_ALGORITHM,
        challenge=challenge,
        salt=salt,
        signature=_sign(f"{challenge}.{salt}.{maxnumber}"),
        maxnumber=maxnumber,
    )


def _issued_at(salt: str) -> float:
    try:
        return float(salt.rsplit(".", 1)[1])
    except (IndexError, ValueError) as exc:
        raise CaptchaError("Malformed captcha challenge.") from exc


def verify_solution(solution: dict | None, *, now: float | None = None) -> None:
    """Accept *solution*, or raise :class:`CaptchaError` saying why not.

    Order matters here: the signature is checked before anything derived from
    the payload is trusted, and the replay claim is made only once the solution
    is known to be otherwise valid — claiming earlier would let an attacker
    burn a victim's in-flight challenge by submitting garbage against it.
    """
    if not settings.captcha_enabled:
        return
    if not isinstance(solution, dict):
        raise CaptchaError("Captcha solution is missing.")

    checked_at = time.time() if now is None else now
    challenge = solution.get("challenge")
    salt = solution.get("salt")
    signature = solution.get("signature")
    number = solution.get("number")

    if not all(isinstance(v, str) and v for v in (challenge, salt, signature)):
        raise CaptchaError("Captcha solution is incomplete.")
    if not isinstance(number, int) or isinstance(number, bool) or number < 0:
        raise CaptchaError("Captcha solution is malformed.")
    if number > _MAX_SUBMITTED_NUMBER:
        raise CaptchaError("Captcha solution is out of range.")

    expected = _sign(f"{challenge}.{salt}.{settings.captcha_max_number}")
    if not hmac.compare_digest(expected, signature):
        raise CaptchaError("Captcha challenge was not issued by this server.")

    if checked_at - _issued_at(salt) > settings.captcha_ttl_seconds:
        raise CaptchaError("Captcha challenge has expired. Please try again.")

    if not hmac.compare_digest(_digest(salt, number), challenge):
        raise CaptchaError("Captcha solution is incorrect.")

    if not _replay_guard.claim(signature, now=checked_at):
        raise CaptchaError("Captcha challenge was already used.")


def reset_replay_guard() -> None:
    """Forget spent challenges — for tests."""
    _replay_guard.reset()
