"""Every deployment variable is either forwarded or declared default-only.

Four times now a setting has been configured in GitHub and silently ignored in
production, because the deploy never passed it on and nothing reported that it
had no effect:

- #617 — `strava_encryption_key` was never forwarded; the deploy failed naming a
  variable nobody had ever set. It appeared only inside a `default()` chain,
  which is why this module parses whole Jinja expressions rather than just the
  name after `{{`.
- #684 — the three Authelia secrets were absent from the workflow entirely, so
  the template rendered them empty and compose substituted placeholders that are
  published in this repository.
- #694 — `ALLOW_ADMIN_AI_KEY_FALLBACK` was set to `false` in the production
  environment and never forwarded, so a deploy re-rendered `.env` with the
  template default `true` and put the owner's provider key back within reach of
  every registered account.
- #700 — `OPS_DISPATCH_TOKEN` was an environment secret read by a job with no
  `environment:`, so it came back as the empty string and the cutover to the
  private ops repository silently did not happen.

The shape is always the same and always silent: setting the value in GitHub
feels like the whole job.

What changed at #700
--------------------
The deploy now runs from the private `ai-trainer-ops` repository (#699), so the
workflow is no longer visible here and this test can no longer read it. The
forwarding contract lives in `deploy/forwarded-vars.yml` instead, and
`deploy/render_extra_vars.py` is what applies it — both in this repository, both
reviewable, both tested below. The ops workflow passes whole `secrets` and
`vars` contexts and names nothing, so the per-name `env:` block that kept being
forgotten no longer exists.

This module does not require everything to be forwarded — most of the template
is deliberately default-only, and demanding a GitHub variable for `postgres_db`
would be noise. It requires each variable to be *one or the other*, so adding a
tunable setting forces the choice to be made and written down.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from types import ModuleType

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "deploy" / "ansible" / "templates" / "app.env.j2"
MANIFEST = REPO / "deploy" / "forwarded-vars.yml"
RENDERER = REPO / "deploy" / "render_extra_vars.py"
DEPLOY_DIR = REPO / "deploy"

# The only non-variable identifiers that appear in app.env.j2's expressions.
# Kept explicit rather than filtered heuristically: a new filter showing up here
# should make someone look, because it means the template grew logic.
JINJA_BUILTINS = {"default", "undef"}


def _renderer() -> ModuleType:
    """Import deploy/render_extra_vars.py, which is outside the backend package."""
    spec = importlib.util.spec_from_file_location("render_extra_vars", RENDERER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def renderer() -> ModuleType:
    return _renderer()


@pytest.fixture(scope="module")
def manifest(renderer: ModuleType) -> dict:
    return renderer.load_manifest()


def _template_variables() -> set[str]:
    """Every variable app.env.j2 reads, including inside `default()` chains.

    Matching only `{{ name` would miss exactly the #617 case: a variable that
    appears solely as a fallback inside another variable's default chain.
    """
    names: set[str] = set()
    for expression in re.findall(r"\{\{(.*?)\}\}", TEMPLATE.read_text(), re.DOTALL):
        # Drop string literals first, or 'HS256' and URL fragments become
        # identifiers.
        expression = re.sub(r"'[^']*'", "''", expression)
        expression = re.sub(r'"[^"]*"', '""', expression)
        names.update(re.findall(r"[a-z_][a-z0-9_]*", expression))
    return names - JINJA_BUILTINS


def _deploy_jinja_reads() -> set[str]:
    """Every name read by anything under deploy/ — template and playbook.

    The template half uses the thorough parse, because `server_url_2` is read
    *only* inside `frontend_url`'s default chain and the loose pattern below
    does not see it. The playbook half stays loose: it picks up play-local names
    like `app_dir` too, which is harmless where this is used (a superset can
    only make the dead-forwarding check more lenient, never wrong).

    A name mentioned in a `{# comment #}` is deliberately *not* a read — that is
    what exposed `server_ip`.
    """
    names = _template_variables()
    for path in DEPLOY_DIR.rglob("*"):
        if path.suffix not in {".j2", ".yml"}:
            continue
        names.update(
            re.findall(r"\{\{\s*([a-z_][a-z0-9_]*)\s*[|}.]", path.read_text())
        )
    return names


def _forwarded(manifest: dict) -> set[str]:
    return {entry["var"] for entry in manifest["forwarded"]}


def _default_only(manifest: dict) -> set[str]:
    return set(manifest["template_default_only"])


# ---------------------------------------------------------------------------
# The manifest agrees with the template
# ---------------------------------------------------------------------------


def test_the_manifest_is_well_formed(renderer: ModuleType):
    """load_manifest validates modes, sources and fallbacks, and must pass.

    Asserted as its own case because every test below reads the manifest: if it
    is malformed they would all fail with the same unhelpful error.
    """
    renderer.load_manifest()


def test_every_template_variable_is_forwarded_or_declared_default_only(manifest: dict):
    unaccounted = _template_variables() - _forwarded(manifest) - _default_only(manifest)

    assert not unaccounted, (
        "These variables are read by app.env.j2 but are neither forwarded nor "
        f"listed as deliberately default-only: {sorted(unaccounted)}. Setting "
        "one of them in the GitHub environment would have no effect and nothing "
        "would say so — that is #617, #684 and #694. Either add it to "
        "`forwarded` in deploy/forwarded-vars.yml with a mode and a reason, or "
        "to `template_default_only` with a note on why it is not tunable."
    )


def test_the_default_only_list_has_not_gone_stale(manifest: dict):
    """A name that leaves the template must leave this list too.

    Otherwise the list grows into a graveyard and starts excusing variables that
    no longer exist, which is how it would quietly stop catching anything.
    """
    stale = _default_only(manifest) - _template_variables()

    assert not stale, (
        f"template_default_only names variables app.env.j2 no longer reads: "
        f"{sorted(stale)}. Remove them from deploy/forwarded-vars.yml."
    )


def test_forwarding_has_not_gone_dead(manifest: dict):
    """Every forwarded variable is actually read by something under deploy/.

    Forwarding a variable nothing reads is not harmless: it reads as though the
    setting is wired up. `server_ip` was in this state — passed on every deploy,
    unread since #682 removed the bare-IP CORS origin — and writing this list
    down is what made it visible.
    """
    dead = _forwarded(manifest) - _deploy_jinja_reads()

    assert not dead, (
        f"These variables are forwarded but nothing under deploy/ reads them: "
        f"{sorted(dead)}. Either the template lost a line or the forwarding "
        f"outlived its use — remove it from deploy/forwarded-vars.yml."
    )


def test_nothing_is_both_forwarded_and_default_only(manifest: dict):
    """The two lists are a decision, so an entry in both is an unmade one."""
    both = _forwarded(manifest) & _default_only(manifest)

    assert not both, (
        f"{sorted(both)} appear in both `forwarded` and `template_default_only`. "
        "A variable is either tunable per deployment or it is not."
    )


def test_the_spend_controls_are_forwarded(manifest: dict):
    """The specific regression: controls that must not revert on deploy.

    Kept as its own case because the general test above would also pass if
    somebody "fixed" a failure by adding these to `template_default_only`, which
    would restore exactly the behaviour #694 was about.
    """
    forwarded = _forwarded(manifest)
    assert "allow_admin_ai_key_fallback" in forwarded
    assert "ai_token_budget" in forwarded


def test_the_secrets_that_must_never_render_empty_are_required(manifest: dict):
    """#684: an empty render here means compose substitutes a published placeholder.

    `optional` on any of these would put the placeholder path back, and the
    deploy would report success while doing it.
    """
    modes = {entry["var"]: entry["mode"] for entry in manifest["forwarded"]}
    for var in (
        "authelia_session_secret",
        "authelia_storage_encryption_key",
        "authelia_identity_validation_reset_password_jwt_secret",
        "postgres_password",
        "jwt_secret",
        "secrets_encryption_key",
    ):
        assert modes[var] == "required", (
            f"{var} is {modes[var]!r}, but an empty value for it is not a valid "
            "deployment state — it is the published-placeholder path #682 closed, "
            "or plaintext secret storage (#612)."
        )


# ---------------------------------------------------------------------------
# The renderer applies the manifest the way the manifest says it does
# ---------------------------------------------------------------------------


def _contexts(**overrides: str) -> tuple[dict[str, str], dict[str, str]]:
    """A complete, valid set of inputs, so a test can knock out one value.

    Built from the manifest rather than hand-listed: a new required input then
    shows up as a real failure here instead of being silently untested.
    """
    manifest = _renderer().load_manifest()
    secrets: dict[str, str] = {}
    variables: dict[str, str] = {}
    entries = manifest["forwarded"] + manifest["workflow_only"]
    for entry in entries:
        target = secrets if entry["from"] == "secret" else variables
        target[entry["name"]] = f"value-for-{entry['name'].lower()}"
    for name, value in overrides.items():
        if name in secrets:
            secrets[name] = value
        else:
            variables[name] = value
    return secrets, variables


def test_a_full_set_of_inputs_produces_no_missing(renderer: ModuleType, manifest: dict):
    secrets, variables = _contexts()
    extra_vars, missing = renderer.build_extra_vars(manifest, secrets, variables)

    assert missing == []
    # Every forwarded variable is present, because every input was supplied.
    assert set(extra_vars) == _forwarded(manifest)


@pytest.mark.parametrize(
    "name",
    [
        "SECRETS_ENCRYPTION_KEY",
        "AUTHELIA_SESSION_SECRET",
        "AUTHELIA_STORAGE_ENCRYPTION_KEY",
        "AUTHELIA_IDENTITY_VALIDATION_RESET_PASSWORD_JWT_SECRET",
        "POSTGRES_PASSWORD",
        "JWT_SECRET",
        "SERVER_URL",
        "SSH_KEY",
    ],
)
def test_a_missing_required_input_is_reported_by_name(
    renderer: ModuleType, manifest: dict, name: str
):
    """The failure has to name the thing to set.

    #617 is the counter-example: the deploy failed naming `strava_encryption_key`,
    a variable nobody had ever set, and sent whoever read it looking in the wrong
    place entirely.
    """
    secrets, variables = _contexts(**{name: ""})
    _, missing = renderer.build_extra_vars(manifest, secrets, variables)

    assert any(name in line for line in missing), (
        f"{name} is empty but the renderer did not report it. A required input "
        "that goes unreported is the whole failure class this guards."
    )


def test_whitespace_only_counts_as_missing(renderer: ModuleType, manifest: dict):
    """A secret pasted with a stray newline must not pass as configured."""
    secrets, variables = _contexts(SECRETS_ENCRYPTION_KEY="   \n  ")
    _, missing = renderer.build_extra_vars(manifest, secrets, variables)

    assert any("SECRETS_ENCRYPTION_KEY" in line for line in missing)


def test_optional_inputs_render_empty_rather_than_missing(
    renderer: ModuleType, manifest: dict
):
    secrets, variables = _contexts(ADMIN_TOTP_SECRET="", STRAVA_CLIENT_ID="")
    extra_vars, missing = renderer.build_extra_vars(manifest, secrets, variables)

    assert missing == []
    assert extra_vars["admin_totp_secret"] == ""
    assert extra_vars["strava_client_id"] == ""


def test_omit_if_empty_inputs_are_left_out_entirely(
    renderer: ModuleType, manifest: dict
):
    """Passing "" would render an empty value, which pydantic rejects at boot.

    The variable has to be *absent* from extra-vars so app.env.j2's own
    `default(...)` is what applies.
    """
    secrets, variables = _contexts(AI_TOKEN_BUDGET="", ALLOW_ADMIN_AI_KEY_FALLBACK="")
    extra_vars, missing = renderer.build_extra_vars(manifest, secrets, variables)

    assert missing == []
    assert "ai_token_budget" not in extra_vars
    assert "allow_admin_ai_key_fallback" not in extra_vars


def test_a_set_spend_control_is_forwarded_verbatim(
    renderer: ModuleType, manifest: dict
):
    """The #694 regression, from the other side: `false` must survive the render."""
    secrets, variables = _contexts(
        ALLOW_ADMIN_AI_KEY_FALLBACK="false", AI_TOKEN_BUDGET="25000000"
    )
    extra_vars, _ = renderer.build_extra_vars(manifest, secrets, variables)

    assert extra_vars["allow_admin_ai_key_fallback"] == "false"
    assert extra_vars["ai_token_budget"] == "25000000"


def test_fallback_inputs_use_their_literal(renderer: ModuleType, manifest: dict):
    secrets, variables = _contexts(APP_UID="", APP_GID="")
    extra_vars, missing = renderer.build_extra_vars(manifest, secrets, variables)

    assert missing == []
    assert extra_vars["app_uid"] == "1000"
    assert extra_vars["app_gid"] == "1000"


def test_workflow_only_inputs_never_reach_the_playbook(
    renderer: ModuleType, manifest: dict
):
    """They are validated, not forwarded.

    `server_ip` used to be forwarded as an extra-var that nothing read; keeping
    these out of the output is what stops that growing back.
    """
    secrets, variables = _contexts()
    extra_vars, _ = renderer.build_extra_vars(manifest, secrets, variables)

    for entry in manifest["workflow_only"]:
        assert entry["name"].lower() not in extra_vars


# ---------------------------------------------------------------------------
# Context handling
# ---------------------------------------------------------------------------


def test_an_absent_context_is_an_error_not_an_empty_deploy(renderer: ModuleType):
    """A missing context must not read as "nothing is configured".

    Treating it as an empty dict would render every optional value empty and
    fail every required one — but if the required list ever shrank, it would
    instead produce a *successful* deploy with the placeholder credentials.
    That is #684 reached from a different direction.
    """
    with pytest.raises(SystemExit) as excinfo:
        renderer._context("DEPLOY_SECRETS", None)

    assert "DEPLOY_SECRETS" in str(excinfo.value)


def test_a_malformed_context_is_an_error(renderer: ModuleType):
    with pytest.raises(SystemExit):
        renderer._context("DEPLOY_VARS", "{not json")
    with pytest.raises(SystemExit):
        renderer._context("DEPLOY_VARS", '["a", "list"]')


def test_context_values_are_coerced_to_strings(renderer: ModuleType):
    """GitHub renders contexts as strings, but a null must not become "None"."""
    parsed = renderer._context("DEPLOY_VARS", json.dumps({"A": None, "B": 5}))

    assert parsed == {"A": "", "B": "5"}


# ---------------------------------------------------------------------------
# The manifest validator rejects the mistakes it exists to reject
# ---------------------------------------------------------------------------


def _write_manifest(tmp_path: Path, entry: dict) -> Path:
    path = tmp_path / "forwarded-vars.yml"
    path.write_text(
        yaml.safe_dump({"forwarded": [entry], "template_default_only": []}),
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize(
    ("entry", "reason"),
    [
        (
            {"var": "x", "from": "secret", "name": "X", "mode": "required"},
            "missing 'why' — an entry with no reason is the thing this file is for",
        ),
        (
            {"var": "x", "from": "secret", "name": "X", "mode": "maybe", "why": "w"},
            "unknown mode",
        ),
        (
            {"var": "x", "from": "envvar", "name": "X", "mode": "required", "why": "w"},
            "unknown source",
        ),
        (
            {"var": "x", "from": "variable", "name": "X", "mode": "fallback", "why": "w"},
            "fallback mode with no fallback value",
        ),
        (
            {
                "var": "x",
                "from": "variable",
                "name": "X",
                "mode": "fallback",
                "fallback": "",
                "why": "w",
            },
            "empty fallback is indistinguishable from `optional`",
        ),
        (
            {
                "var": "x",
                "from": "variable",
                "name": "X",
                "mode": "optional",
                "fallback": "1000",
                "why": "w",
            },
            "fallback value that the mode means nothing will ever read",
        ),
    ],
)
def test_a_malformed_manifest_entry_is_rejected(
    renderer: ModuleType, tmp_path: Path, entry: dict, reason: str
):
    with pytest.raises(renderer.ManifestError):
        renderer.load_manifest(_write_manifest(tmp_path, entry))


def test_the_real_manifest_declares_a_reason_for_every_entry(manifest: dict):
    """Not enforced by load_manifest's truthiness check alone: " " would pass.

    The reasons are the reason this file beats a list in a workflow — they are
    what tells the next person whether `optional` was a decision or a guess.
    """
    for entry in manifest["forwarded"] + manifest["workflow_only"]:
        name = entry.get("var") or entry["name"]
        assert len(entry["why"].strip()) > 20, f"{name} needs a real reason, not {entry['why']!r}"


def test_the_frontend_calls_the_api_on_its_own_origin():
    """Production serves the app on several domains (ai-trainer-ops#45).

    A fixed VITE_BACKEND_URL makes every other domain cross-site: CSP
    `connect-src 'self'` blocks its API calls, and a SameSite session cookie
    would not be sent. Empty means same origin, and compose must pass the empty
    value through rather than fall back to localhost (`:-` would).
    """
    assert "VITE_BACKEND_URL={{ vite_backend_url | default('') }}" in TEMPLATE.read_text()
    compose = (REPO / "compose.yml").read_text()
    assert "VITE_BACKEND_URL: ${VITE_BACKEND_URL-http://localhost:8000}" in compose
