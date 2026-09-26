"""Every deployment variable is either forwarded or declared default-only (#694).

Three times now a setting has been configured in the GitHub environment and
silently ignored in production, because `app.env.j2` reads a variable the
deploy workflow never passes and Jinja quietly falls back to `default(...)`:

- #617 — `strava_encryption_key` was never forwarded; the deploy failed naming
  a variable nobody had ever set
- #684 — the three Authelia secrets were absent from the workflow entirely, so
  the template rendered them empty and compose substituted placeholders that
  are published in this repository
- #694 — `ALLOW_ADMIN_AI_KEY_FALLBACK` was set to `false` in the production
  environment and never forwarded, so a deploy re-rendered `.env` with the
  template default `true` and put the owner's provider key back within reach of
  every registered account

The shape is always the same and always silent: setting the variable in GitHub
feels like the whole job, and nothing anywhere reports that it had no effect.

This test does not require everything to be forwarded — most of the template is
deliberately default-only, and demanding a GitHub variable for
``postgres_db`` would be noise. It requires each variable to be *one or the
other*, so adding a new tunable setting forces the choice to be made and
written down rather than assumed.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "deploy" / "ansible" / "templates" / "app.env.j2"
WORKFLOW = REPO / ".github" / "workflows" / "deploy.yml"

# Variables the playbook is expected to supply from its own defaults, with no
# way to override them per deployment. Adding a name here is a decision that
# this setting is not worth exposing; if that turns out to be wrong, forward it
# in the workflow instead and delete the entry.
TEMPLATE_DEFAULT_ONLY = {
    # Identity and naming — changing these would need a database migration or a
    # DNS change, so a per-deploy override would be a trap rather than a knob.
    "postgres_db",
    "postgres_user",
    "app_env",
    # Derived from server_url, which *is* forwarded.
    "backend_url",
    "frontend_url",
    "vite_backend_url",
    "vite_authelia_url",
    # Protocol constants. The Authelia header names have to agree with the
    # Traefik middleware in compose.yml, so they are not independently tunable.
    "jwt_algorithm",
    "jwt_expire_minutes",
    "authelia_auth_enabled",
    "authelia_remote_user_header",
    "authelia_remote_email_header",
    "authelia_remote_name_header",
    # SMTP is unconfigured on this deployment (the notifier writes to a file).
    # When it is set up, these move out of this set — see #687.
    "authelia_notifier_smtp_address",
    "authelia_notifier_smtp_username",
    "authelia_notifier_smtp_password",
    "authelia_notifier_smtp_sender",
    "authelia_notifier_smtp_identifier",
    "authelia_notifier_smtp_startup_check_address",
    # Abuse and spend limits whose defaults are the policy. The two that are
    # genuinely deployment-specific — the token budget and the admin-key
    # fallback — are forwarded instead.
    "ai_rate_limit_enabled",
    "ai_token_budget_window_days",
    "auth_rate_limit_enabled",
    "admin_login_rate_limit_attempts",
    "admin_login_rate_limit_seconds",
    "registration_rate_limit_attempts",
    "registration_rate_limit_seconds",
    "captcha_enabled",
    "captcha_max_number",
    # Second factor (#688). The issuer is a display string in the authenticator
    # app and the trust window is policy — neither is deployment-specific. The
    # one secret here, admin_totp_secret, *is* forwarded.
    "totp_issuer",
    "trusted_device_days",
    # Reachable only through an SSH tunnel; Grafana forces a change on first
    # login when this is empty (#549).
    "grafana_admin_password",
}


def _template_variables() -> set[str]:
    """Every Jinja variable the env template reads."""
    return set(re.findall(r"\{\{\s*([a-z_][a-z0-9_]*)\s*[|}]", TEMPLATE.read_text()))


def _forwarded_variables() -> set[str]:
    """Every extra_var the deploy workflow passes to the playbook."""
    return set(re.findall(r'"([a-z_][a-z0-9_]*)":\s*os\.environ', WORKFLOW.read_text()))


def test_every_template_variable_is_forwarded_or_declared_default_only():
    unaccounted = _template_variables() - _forwarded_variables() - TEMPLATE_DEFAULT_ONLY

    assert not unaccounted, (
        "These variables are read by app.env.j2 but neither forwarded by the "
        "deploy workflow nor listed as deliberately default-only: "
        f"{sorted(unaccounted)}. Setting one of them in the GitHub environment "
        "would have no effect and nothing would say so — that is #617, #684 "
        "and #694. Either forward it in .github/workflows/deploy.yml (both the "
        "`env:` block and the extra_vars dict) or add it to "
        "TEMPLATE_DEFAULT_ONLY with a note on why it is not tunable."
    )


def test_the_default_only_list_has_not_gone_stale():
    """A name that leaves the template must leave this list too.

    Otherwise the allow-list grows into a graveyard and starts excusing
    variables that no longer exist, which is how it would quietly stop
    catching anything.
    """
    stale = TEMPLATE_DEFAULT_ONLY - _template_variables()

    assert not stale, (
        f"TEMPLATE_DEFAULT_ONLY names variables app.env.j2 no longer reads: "
        f"{sorted(stale)}. Remove them."
    )


def test_the_admin_key_fallback_is_forwarded():
    """The specific regression: a spend control that must not revert on deploy.

    Kept as its own case because the general test above would also pass if
    somebody "fixed" it by adding the name to TEMPLATE_DEFAULT_ONLY, which
    would restore exactly the behaviour #694 was about.
    """
    assert "allow_admin_ai_key_fallback" in _forwarded_variables()
    assert "ai_token_budget" in _forwarded_variables()
