"""Every backend setting is in .env.example, or named here with the reason it is not
(ai-trainer-ops#290).

.env.example covered 41 of the backend's 102 settings. ADMIN_PASSWORD,
NTFY_URL, CAPTCHA_ENABLED and REQUIRE_PRIVACY_CONSENT were missing, although
compose.yml passes every one of them to the backend. And AI_STUB_PROVIDER was
offered, although compose.yml deliberately never passes it on, so setting it did
nothing.

The file is what `docker compose up` reads, so it lists what reaches the backend
from a `.env`. A new setting has to be added there, or named here with its
reason. That shape follows test_deploy_wiring.py.
"""

from __future__ import annotations

import re
from pathlib import Path

from config import Settings

REPO = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = REPO / ".env.example"
COMPOSE = REPO / "compose.yml"

NOT_FORWARDED = (
    "compose.yml does not pass it to the backend, so a value in .env has no effect; "
    "a limit, a window or a lifetime that stays at its code default (ai-trainer-ops#256)"
)

NOT_IN_EXAMPLE: dict[str, str] = {
    "database_url": "compose.yml builds it from POSTGRES_* in .env.example",
    "git_sha": "the image build and the deploy set it",
    "strava_encryption_key": "the old name of SECRETS_ENCRYPTION_KEY, still read; .env.example names it in a comment",
    "authelia_internal_url": "compose.yml's own address of its authelia service",
    "authelia_users_db_path": "compose.yml's own mount of the user store",
    "authelia_proxy_secret_header": "the header compose.yml's Traefik injects, changed only together with it",
    "ai_stub_provider": "compose.yml must not pass it on (test_stub_provider.py); the end-to-end stacks set it",
    "captcha_challenge_rate_limit_attempts": NOT_FORWARDED,
    "captcha_challenge_rate_limit_seconds": NOT_FORWARDED,
    "duration_refresh_lookback_days": NOT_FORWARDED,
    "email_verification_link_hours": NOT_FORWARDED,
    "password_reset_rate_limit_attempts": NOT_FORWARDED,
    "password_reset_rate_limit_global_attempts": NOT_FORWARDED,
    "password_reset_rate_limit_global_seconds": NOT_FORWARDED,
    "password_reset_rate_limit_seconds": NOT_FORWARDED,
    "password_reset_token_minutes": NOT_FORWARDED,
    "power_curve_rate_limit_burst": NOT_FORWARDED,
    "power_curve_rate_limit_hourly": NOT_FORWARDED,
    "totp_challenge_ttl_seconds": NOT_FORWARDED,
    "totp_code_rate_limit_attempts": NOT_FORWARDED,
    "totp_code_rate_limit_seconds": NOT_FORWARDED,
    "unverified_account_days": NOT_FORWARDED,
}


def _example_names() -> set[str]:
    return {
        name.lower()
        for name in re.findall(r"^([A-Z][A-Z0-9_]*)=", ENV_EXAMPLE.read_text(), re.M)
    }


def _forwarded_names() -> set[str]:
    """The keys of the backend service's ``environment:`` mapping in compose.yml."""
    service = re.search(
        r"^  backend:\n(.*?)(?=^  \S)", COMPOSE.read_text(), re.M | re.S
    )
    assert service, "compose.yml has no backend service"
    environment = re.search(
        r"^    environment:\n((?:^      .*\n|^\s*\n)*)", service.group(1), re.M
    )
    assert environment, "the backend service has no environment mapping"
    return {
        name.lower()
        for name in re.findall(r"^      ([A-Z][A-Z0-9_]*):", environment.group(1), re.M)
    }


SETTINGS = set(Settings.model_fields)


def test_every_setting_is_in_the_example_or_left_out_with_a_reason():
    missing = sorted(SETTINGS - _example_names() - set(NOT_IN_EXAMPLE))
    assert missing == [], (
        "Add these to .env.example with their default and a line on what they do, or to "
        "NOT_IN_EXAMPLE with the reason they are left out: "
        + ", ".join(name.upper() for name in missing)
    )


def test_a_setting_in_the_example_is_not_also_left_out():
    assert sorted(set(NOT_IN_EXAMPLE) & _example_names()) == []


def test_the_left_out_names_are_settings():
    assert sorted(set(NOT_IN_EXAMPLE) - SETTINGS) == []


def test_every_setting_in_the_example_reaches_the_backend():
    """The example is what `docker compose up` reads: a setting compose does not pass on would do nothing."""
    assert sorted((_example_names() & SETTINGS) - _forwarded_names()) == []


def test_a_setting_listed_as_not_forwarded_is_not_forwarded():
    """Once compose passes one on (ai-trainer-ops#256), it belongs in the example instead."""
    not_forwarded = {
        name for name, reason in NOT_IN_EXAMPLE.items() if reason == NOT_FORWARDED
    }
    assert sorted(not_forwarded & _forwarded_names()) == []
