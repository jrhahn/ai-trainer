"""Which settings production can change, and why the rest stay at their code
default (ai-trainer-ops#256).

A setting reaches the backend in production through three layers, and a gap in
any of them leaves it on the code default with nothing to say so:

1. deploy/forwarded-vars.yml turns a Forgejo variable into an extra-var;
2. deploy/ansible/templates/app.env.j2 writes the extra-var into .env;
3. compose.yml passes the .env line into the backend container.

Half the settings were in neither of the first two, the model names among
them, so a new coach model needed a code release. The operational settings now go through all
three, with the backend's own default at each: the manifest leaves an unset
variable out, the template then writes its default, and compose passes that
on. A deploy with none of these variables set renders what the code would use
anyway.

Every other setting is written by the template, set by compose.yml itself, or
named here with the reason it stays at its code default.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest
from pydantic import TypeAdapter, ValidationError

from config import Settings

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "deploy" / "ansible" / "templates" / "app.env.j2"
COMPOSE = REPO / "compose.yml"
RENDERER = REPO / "deploy" / "render_extra_vars.py"

FIELDS = Settings.model_fields

# What the operator changes with a Forgejo variable, without a code release.
OPERATIONAL = (
    "gemini_classify_model",
    "gemini_plan_model",
    "gemini_coach_model",
    "gemini_feedback_model",
    "openai_classify_model",
    "openai_plan_model",
    "openai_coach_model",
    "openai_feedback_model",
    "embedding_provider",
    "gemini_embedding_model",
    "openai_embedding_model",
    "ai_rate_limit_enabled",
    "ai_rate_limit_burst",
    "ai_rate_limit_burst_seconds",
    "ai_rate_limit_sustained",
    "ai_rate_limit_sustained_seconds",
    "activity_sync_interval_seconds",
    "app_timezone",
    "email_verification_required",
    "log_llm_payloads",
    "continuous_learning_enabled",
    "readiness_observation_matching",
)

# Set by compose.yml from its own services; no deployment chooses these.
COMPOSE_ONLY: dict[str, str] = {
    "database_url": "compose.yml builds it from POSTGRES_* and its postgres service",
    "authelia_internal_url": "compose.yml's own address of its authelia service",
    "authelia_users_db_path": "compose.yml's own mount of the user store",
    "authelia_proxy_secret_header": "the header compose.yml's Traefik injects, changed only with it",
}

_LIMIT = "an abuse limit whose default is the policy, as the manifest's other limits"
_LIFETIME = "a lifetime or a size cap that stays a code default (ai-trainer-ops#256)"

# Not written by the template, so production runs the code default, or
# compose's fallback where compose passes one, which is the same value
# (test_compose_defaults_256.py). Meant to be so.
CODE_DEFAULT: dict[str, str] = {
    "ai_stub_provider": "canned AI answers for the end-to-end stacks; no deployment may switch them on",
    "strava_encryption_key": "the pre-#613 name of SECRETS_ENCRYPTION_KEY, read inside that line's default()",
    "duration_refresh_lookback_days": "follows intervals.icu's delay in computing moving_time, not the host",
    "captcha_challenge_rate_limit_attempts": _LIMIT,
    "captcha_challenge_rate_limit_seconds": _LIMIT,
    "login_rate_limit_attempts": _LIMIT,
    "login_rate_limit_global_attempts": _LIMIT,
    "login_rate_limit_global_seconds": _LIMIT,
    "login_rate_limit_seconds": _LIMIT,
    "password_reset_rate_limit_attempts": _LIMIT,
    "password_reset_rate_limit_global_attempts": _LIMIT,
    "password_reset_rate_limit_global_seconds": _LIMIT,
    "password_reset_rate_limit_seconds": _LIMIT,
    "power_curve_rate_limit_burst": _LIMIT,
    "power_curve_rate_limit_hourly": _LIMIT,
    "totp_code_rate_limit_attempts": _LIMIT,
    "totp_code_rate_limit_seconds": _LIMIT,
    "captcha_ttl_seconds": _LIFETIME,
    "email_verification_link_hours": _LIFETIME,
    "password_reset_token_minutes": _LIFETIME,
    "totp_challenge_ttl_seconds": _LIFETIME,
    "unverified_account_days": _LIFETIME,
    "fit_upload_bulk_max_files": _LIFETIME,
    "fit_upload_max_bytes": _LIFETIME,
    "max_request_body_bytes": _LIFETIME,
}


def _renderer() -> ModuleType:
    """Import deploy/render_extra_vars.py, which is outside the backend package."""
    spec = importlib.util.spec_from_file_location("render_extra_vars_256", RENDERER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RENDER = _renderer()
MANIFEST = RENDER.load_manifest()


def _template_defaults() -> dict[str, tuple[str, str]]:
    """``KEY={{ var | default('x') }}`` in app.env.j2: the extra-var and its default, by key."""
    lines = re.findall(
        r"^([A-Z][A-Z0-9_]*)=\{\{ ([a-z_][a-z0-9_]*) \| default\('([^']*)'\) \}\}$",
        TEMPLATE.read_text(),
        re.M,
    )
    return {key: (var, default) for key, var, default in lines}


def _template_keys() -> set[str]:
    """Every setting app.env.j2 writes a line for, whatever the expression."""
    return {
        key.lower()
        for key in re.findall(r"^([A-Z][A-Z0-9_]*)=", TEMPLATE.read_text(), re.M)
    }


def _compose_fallbacks() -> dict[str, str]:
    """``KEY: ${KEY:-fallback}`` in the backend service's environment, by key."""
    service = re.search(
        r"^  backend:\n(.*?)(?=^  \S)", COMPOSE.read_text(), re.M | re.S
    )
    assert service, "compose.yml has no backend service"
    return dict(
        re.findall(
            r"^      ([A-Z][A-Z0-9_]*): \$\{\1:-([^}]*)\}\s*$", service.group(1), re.M
        )
    )


def _compose_keys() -> set[str]:
    service = re.search(
        r"^  backend:\n(.*?)(?=^  \S)", COMPOSE.read_text(), re.M | re.S
    )
    assert service, "compose.yml has no backend service"
    return {
        key.lower()
        for key in re.findall(r"^      ([A-Z][A-Z0-9_]*):", service.group(1), re.M)
    }


def _is_code_default(name: str, value: str) -> bool:
    """Whether ``value``, read as the setting reads it, is the code's default."""
    field = FIELDS[name]
    try:
        return TypeAdapter(field.annotation).validate_python(value) == field.default
    except ValidationError:
        return False


def _entry(name: str) -> dict:
    entries = [entry for entry in MANIFEST["forwarded"] if entry["var"] == name]
    assert len(entries) == 1, (
        f"deploy/forwarded-vars.yml has {len(entries)} entries for {name}"
    )
    return entries[0]


def _env_line(name: str, extra_vars: dict[str, str]) -> str:
    """The value app.env.j2 writes for ``name``: the extra-var, or else the default."""
    var, default = _template_defaults()[name.upper()]
    return extra_vars.get(var, default)


def _full_inputs() -> tuple[dict[str, str], dict[str, str]]:
    """Every required input present, so a render cannot fail on something else."""
    contexts: dict[str, dict[str, str]] = {"secret": {}, "variable": {}}
    for entry in MANIFEST["forwarded"] + MANIFEST["workflow_only"]:
        if entry["mode"] == "required":
            contexts[entry["from"]][entry["name"]] = "set"
    return contexts["secret"], contexts["variable"]


# ---------------------------------------------------------------------------
# The operational settings, through all three layers
# ---------------------------------------------------------------------------


def test_the_operational_settings_are_settings():
    assert sorted(set(OPERATIONAL) - set(FIELDS)) == []


def test_every_model_and_ai_rate_limit_setting_is_operational():
    """A new model or rate-limit setting is settable from its first deploy."""
    expected = {
        name
        for name in FIELDS
        if name.endswith(("_model", "_provider")) or name.startswith("ai_rate_limit_")
    } - {"ai_stub_provider"}
    assert sorted(expected - set(OPERATIONAL)) == []


@pytest.mark.parametrize("name", OPERATIONAL)
def test_the_manifest_forwards_it_from_a_variable_and_leaves_it_out_when_unset(name):
    entry = _entry(name)
    assert (entry["from"], entry["name"], entry["mode"]) == (
        "variable",
        name.upper(),
        "omit_if_empty",
    ), (
        "Forward it from the Forgejo variable of the same name with omit_if_empty, so an "
        "unset variable keeps the template's default instead of rendering an empty value"
    )


@pytest.mark.parametrize("name", OPERATIONAL)
def test_the_template_writes_it_with_the_code_default(name):
    lines = _template_defaults()
    assert name.upper() in lines, (
        f"app.env.j2 needs {name.upper()}={{{{ {name} | default('<code default>') }}}}"
    )
    var, default = lines[name.upper()]
    assert var == name
    assert _is_code_default(name, default), (
        f"app.env.j2 defaults {name.upper()} to {default!r}, the code to {FIELDS[name].default!r}"
    )


@pytest.mark.parametrize("name", OPERATIONAL)
def test_compose_passes_it_with_the_code_default(name):
    fallbacks = _compose_fallbacks()
    assert name.upper() in fallbacks, (
        f"compose.yml's backend needs {name.upper()}: ${{{name.upper()}:-<code default>}}"
    )
    assert _is_code_default(name, fallbacks[name.upper()])


def test_a_set_variable_reaches_the_env_line():
    """The operator's path: set the Forgejo variable, and the next deploy writes it."""
    secrets, variables = _full_inputs()
    variables.update({name.upper(): f"value-of-{name}" for name in OPERATIONAL})
    extra_vars, missing = RENDER.build_extra_vars(MANIFEST, secrets, variables)
    assert missing == []
    assert {name: _env_line(name, extra_vars) for name in OPERATIONAL} == {
        name: f"value-of-{name}" for name in OPERATIONAL
    }


@pytest.mark.parametrize("unset", [None, "", "  "], ids=["absent", "empty", "blank"])
def test_an_unset_variable_renders_the_code_default(unset):
    """No variable, or an empty one, must never write an empty value over the default."""
    secrets, variables = _full_inputs()
    if unset is not None:
        variables.update({name.upper(): unset for name in OPERATIONAL})
    extra_vars, missing = RENDER.build_extra_vars(MANIFEST, secrets, variables)
    assert missing == []
    assert sorted(set(OPERATIONAL) & set(extra_vars)) == []
    assert [
        name
        for name in OPERATIONAL
        if not _is_code_default(name, _env_line(name, extra_vars))
    ] == []


# ---------------------------------------------------------------------------
# Every other setting: written, set by compose, or kept at its code default
# ---------------------------------------------------------------------------


def test_every_setting_is_written_set_by_compose_or_kept_at_its_code_default():
    unaccounted = sorted(
        set(FIELDS) - _template_keys() - set(COMPOSE_ONLY) - set(CODE_DEFAULT)
    )
    assert unaccounted == [], (
        "Production would run these on their code default without anyone having decided "
        "it. Forward each through the three layers (deploy/forwarded-vars.yml, app.env.j2, "
        "compose.yml), or name it in CODE_DEFAULT with the reason: "
        + ", ".join(unaccounted)
    )


def test_the_named_settings_are_settings():
    assert sorted((set(COMPOSE_ONLY) | set(CODE_DEFAULT)) - set(FIELDS)) == []


def test_a_named_setting_is_not_also_written_by_the_template():
    """Once the template writes one, its entry here is stale."""
    assert sorted((set(COMPOSE_ONLY) | set(CODE_DEFAULT)) & _template_keys()) == []
    assert sorted(set(COMPOSE_ONLY) & set(CODE_DEFAULT)) == []


def test_the_compose_only_settings_are_set_by_compose():
    assert sorted(set(COMPOSE_ONLY) - _compose_keys()) == []
