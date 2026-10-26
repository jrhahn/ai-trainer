"""compose.yml's fallbacks for the backend's settings match the code's defaults
(ai-trainer-ops#256).

compose.yml passes most settings as ``KEY: ${KEY:-fallback}``. The fallback is
what a stack gets when its .env leaves the key out, so it is a second default,
and it had drifted. REGISTRATION_RATE_LIMIT_ATTEMPTS fell back to 5 there,
while config.py and the deploy template had raised it to 20 for the invite
rounds: a local stack refused the sixth registration of the hour, production
did not.

A fallback that differs from the code on purpose is named below with its reason.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from config import Settings

REPO = Path(__file__).resolve().parents[2]
COMPOSE = REPO / "compose.yml"

# The compose stack is the production shape, with Authelia in front of the app.
# The code's defaults are for a backend on its own, as the tests run it.
DIFFERS_ON_PURPOSE: dict[str, str] = {
    "authelia_auth_enabled": "the compose stack runs behind Authelia; a backend on its own does not",
    "authelia_internal_url": "compose.yml's own address of its authelia service",
    "authelia_users_db_path": "compose.yml's own mount of the user store",
}


def _fallbacks() -> dict[str, str]:
    """``KEY: ${KEY:-fallback}`` in the backend service's environment, by setting name."""
    service = re.search(
        r"^  backend:\n(.*?)(?=^  \S)", COMPOSE.read_text(), re.M | re.S
    )
    assert service, "compose.yml has no backend service"
    pairs = re.findall(
        r"^      ([A-Z][A-Z0-9_]*): \$\{\1:-([^}]*)\}\s*$", service.group(1), re.M
    )
    return {key.lower(): value for key, value in pairs}


FIELDS = Settings.model_fields
FALLBACKS = {name: value for name, value in _fallbacks().items() if name in FIELDS}


def _same(name: str, fallback: str) -> bool:
    """Whether the fallback, read as the setting reads it, is the code's default."""
    field = FIELDS[name]
    try:
        return TypeAdapter(field.annotation).validate_python(fallback) == field.default
    except ValidationError:
        return False


def test_the_fallbacks_are_found():
    """A guard on the parser: one that found nothing would pass everything below."""
    assert len(FALLBACKS) > 50


def test_every_fallback_is_the_code_default_or_differs_on_purpose():
    drift = {
        name: f"compose {fallback!r}, code {FIELDS[name].default!r}"
        for name, fallback in FALLBACKS.items()
        if name not in DIFFERS_ON_PURPOSE and not _same(name, fallback)
    }
    assert drift == {}, (
        "compose.yml falls back to another value than config.py. Make them the same, "
        f"or name the setting in DIFFERS_ON_PURPOSE with its reason: {drift}"
    )


@pytest.mark.parametrize("name", sorted(DIFFERS_ON_PURPOSE))
def test_a_difference_on_purpose_still_differs(name):
    assert name in FALLBACKS, (
        f"compose.yml has no fallback for {name} any more: drop it from DIFFERS_ON_PURPOSE"
    )
    assert not _same(name, FALLBACKS[name]), (
        f"{name} matches the code now: drop it from DIFFERS_ON_PURPOSE"
    )
