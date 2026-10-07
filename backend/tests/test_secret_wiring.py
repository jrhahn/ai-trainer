"""Guards for the path a secret takes from the deploy templates into the app.

#612: ``STRAVA_ENCRYPTION_KEY`` was read correctly by ``Settings`` and used
correctly by ``EncryptedString``, and still every user API key in production was
stored as plaintext — because no line in ``compose.yml`` passed the variable
into the container, and nothing rendered it into the deployed ``.env``.  Python
tests could not see that, so these assertions read the deployment files.

**Adding an optional credential?** The chain is .env.example -> compose.yml ->
app.env.j2 -> forwarded-vars.yml, and it is only as good as its weakest hop.
The last hop used to be the deploy workflow's own `extra_vars` dict; since #700
the deploy runs from a private repository and reads the manifest instead, which
is what keeps this hop checkable from here at all. A *required*
variable that misses a hop crashes the deploy, which the tests below catch. An
optional one rendered with a literal ``default('')`` fails silently instead: the
value is simply empty forever, however carefully the GitHub secret was set.
``SEMANTIC_SCHOLAR_API_KEY`` sat like that until #638. Assert all four hops for
it, the way that PR did — there is no boot guard that will do it for you.
"""

import importlib.util
import re
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "compose.yml"
ANSIBLE_ENV_TEMPLATE = REPO_ROOT / "deploy" / "ansible" / "templates" / "app.env.j2"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
MANIFEST = REPO_ROOT / "deploy" / "forwarded-vars.yml"
RENDERER = REPO_ROOT / "deploy" / "render_extra_vars.py"

def _encryption_key_vars() -> tuple[str, ...]:
    """Every per-purpose encryption key, read from the one place that lists them.

    Derived rather than typed out, because typing it out is how the four keys of
    ai-trainer-ops#12 shipped absent from ``compose.yml``: this guard existed,
    caught exactly this class of bug, and was a hand-maintained tuple of two
    that nobody thought to extend. The whole point of the file it guards is that
    a variable missing from the compose block reads as configured on the host
    and never reaches the process.
    """
    from config import _DEDICATED_KEY_FIELDS

    return tuple(field.upper() for field in _DEDICATED_KEY_FIELDS.values())


# Settings the backend cannot be trusted to run without once it leaves dev.
SECURITY_CRITICAL_VARS = (
    "SECRETS_ENCRYPTION_KEY",
    "APP_ENV",
) + _encryption_key_vars()


def _backend_environment_block() -> str:
    """The ``environment:`` mapping of the backend service, as raw text."""
    text = COMPOSE.read_text()
    start = text.index("\n  backend:")
    end = text.index("\n  frontend:", start)
    service = text[start:end]
    env_start = service.index("\n    environment:")
    return service[env_start:]


@pytest.mark.parametrize("var", SECURITY_CRITICAL_VARS)
def test_compose_passes_variable_into_the_backend_container(var: str) -> None:
    """A value in .env reaches the container only if compose forwards it."""
    assert f"{var}:" in _backend_environment_block(), (
        f"{var} is missing from the backend environment block in compose.yml. "
        "Docker Compose uses .env for variable substitution, so the value will "
        "look present on the host and still never reach the process."
    )


@pytest.mark.parametrize("var", SECURITY_CRITICAL_VARS)
def test_deployment_renders_the_variable(var: str) -> None:
    assert f"{var}=" in ANSIBLE_ENV_TEMPLATE.read_text(), (
        f"{var} is not rendered into the deployed .env by app.env.j2, so the "
        "compose substitution above resolves to its empty default."
    )


@pytest.mark.parametrize("var", SECURITY_CRITICAL_VARS)
def test_env_example_documents_the_variable(var: str) -> None:
    assert f"{var}=" in ENV_EXAMPLE.read_text()


def test_deployed_env_has_no_default_for_the_encryption_key() -> None:
    """Ansible must fail loudly rather than deploy without an encryption key."""
    for line in ANSIBLE_ENV_TEMPLATE.read_text().splitlines():
        if line.startswith("SECRETS_ENCRYPTION_KEY="):
            # Fallbacks onto other *variables* are fine (the pre-#613 name, and
            # undef() to end the chain with a usable message). A fallback onto a
            # literal is not: that is a value, and a deploy holding no key would
            # take it and store plaintext instead of aborting.
            assert not re.search(r"default\(\s*['\"]", line), (
                "A literal default here means a deploy that forgot the secret "
                "silently stores plaintext instead of aborting."
            )
            return
    pytest.fail("SECRETS_ENCRYPTION_KEY is not rendered by app.env.j2")


def _required_template_vars() -> set[str]:
    """Ansible vars ``app.env.j2`` cannot render without.

    A ``| default(...)`` only makes a variable optional when the fallback is a
    literal. When the fallback is another variable the requirement *moves* to it,
    which is why ``secrets_encryption_key | default(strava_encryption_key)`` read
    as optional to everyone who looked at it and was not (#617).
    """
    template = re.sub(r"\{#.*?#\}", "", ANSIBLE_ENV_TEMPLATE.read_text(), flags=re.S)

    def names(expression: str) -> set[str]:
        head = re.match(r"([a-z_][a-z0-9_]*)\s*(.*)", expression.strip(), re.S)
        if not head:
            return set()  # a literal, or something we do not model
        name, rest = head.group(1), head.group(2)
        if rest.startswith("("):
            return set()  # a call such as undef(), not a variable reference
        fallback = re.match(r"\|\s*default\((.*)\)\s*$", rest, re.S)
        if not fallback:
            return {name}
        argument = fallback.group(1).strip()
        if argument.startswith(("'", '"')):
            return set()
        return names(argument)

    required: set[str] = set()
    for expression in re.findall(r"\{\{(.*?)\}\}", template, re.S):
        required |= names(expression)
    return required


def _manifest() -> dict:
    """Load deploy/forwarded-vars.yml through the renderer that validates it."""
    spec = importlib.util.spec_from_file_location("render_extra_vars", RENDERER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.load_manifest()


def test_every_variable_the_template_requires_is_forwarded() -> None:
    """The link nothing checked: GitHub secret -> extra-var -> template.

    #612 wired the encryption key from the template all the way into the
    container and these tests confirmed every hop of it, but no hop above the
    template. The workflow never passed the variable, so the first deploy after
    the rename aborted while rendering .env — on a host it had already rsynced.

    Read from the manifest rather than the workflow since #700: the deploy runs
    from a private repository now, and the forwarding contract lives in
    `deploy/forwarded-vars.yml` precisely so this check can still see it.
    """
    forwarded = {entry["var"] for entry in _manifest()["forwarded"]}
    missing = _required_template_vars() - forwarded
    assert not missing, (
        f"app.env.j2 cannot render without {sorted(missing)} — they have no "
        "literal fallback — and deploy/forwarded-vars.yml does not forward "
        "them. The deploy will fail after the host has been synced."
    )


def test_variables_the_template_requires_are_never_merely_optional() -> None:
    """`optional` renders "" for an absent value, which is not the same as failing.

    For a variable with no literal fallback in the template, an empty extra-var
    is worse than a missing one: Jinja renders the line blank instead of
    stopping, so compose falls through to `${VAR:-default}` and production runs
    on whatever that default is. That is #684 — with placeholders committed to a
    public repository.
    """
    modes = {entry["var"]: entry["mode"] for entry in _manifest()["forwarded"]}
    merely_optional = sorted(
        var for var in _required_template_vars() if modes.get(var) == "optional"
    )
    assert not merely_optional, (
        f"{merely_optional} have no literal fallback in app.env.j2, so an empty "
        "value renders an empty line rather than failing the deploy. Mark them "
        "`required` in deploy/forwarded-vars.yml."
    )


def test_the_encryption_key_is_required_rather_than_merely_forwarded() -> None:
    """Forwarding it with mode `optional` would deploy an empty key as if it were one.

    Pinned by name because the checks above cannot see it: its template entry
    ends in `undef(...)`, so `_required_template_vars` reads it as already
    guarded. It is — at render time, on a host that has already been rsynced.
    This makes it fail in the workflow instead.
    """
    modes = {entry["var"]: entry["mode"] for entry in _manifest()["forwarded"]}
    assert modes["secrets_encryption_key"] == "required"


def test_production_app_env_reaches_the_boot_guard() -> None:
    """The guard in Settings only fires when APP_ENV actually says production."""
    for line in ANSIBLE_ENV_TEMPLATE.read_text().splitlines():
        if line.startswith("APP_ENV="):
            assert "production" in line
            return
    pytest.fail("APP_ENV is not rendered by app.env.j2")


class TestEncryptedString:
    """The type itself: ciphertext when keyed, plaintext fallback when not."""

    def _column(self, purpose: str | None = None):
        from config import SecretPurpose
        from models import EncryptedString

        # Any purpose would do for the behaviour below; these tests are about
        # the type, not the split (#12). Defaults to the one whose column
        # existed first, so the assertions read the same as they did.
        return EncryptedString(purpose or SecretPurpose.STRAVA)

    def test_stores_ciphertext_when_a_key_is_configured(self, monkeypatch) -> None:
        from config import settings

        key = Fernet.generate_key().decode()
        monkeypatch.setattr(settings, "secrets_encryption_key", key)
        column = self._column()

        stored = column.process_bind_param("AIzaSy-not-a-real-key", None)

        assert stored != "AIzaSy-not-a-real-key"
        assert stored.startswith("gAAAAA"), "not a Fernet token"
        assert column.process_result_value(stored, None) == "AIzaSy-not-a-real-key"

    def test_stores_plaintext_without_a_key(self, monkeypatch) -> None:
        """Documents the dev-only behaviour that made #612 possible."""
        from config import settings

        monkeypatch.setattr(settings, "secrets_encryption_key", "")
        monkeypatch.setattr(settings, "strava_encryption_key", "")
        monkeypatch.setattr(settings, "strava_token_encryption_key", "")

        assert self._column().process_bind_param("secret", None) == "secret"

    def test_reports_when_a_stored_secret_cannot_be_decrypted(
        self, monkeypatch, caplog
    ) -> None:
        from config import settings

        monkeypatch.setattr(
            settings, "secrets_encryption_key", Fernet.generate_key().decode()
        )

        with caplog.at_level("WARNING"):
            result = self._column().process_result_value("plaintext-or-wrong-key", None)

        assert result == "plaintext-or-wrong-key"
        assert "Could not decrypt" in caplog.text, (
            "silently swallowing this is what kept the misconfiguration invisible"
        )


class TestProductionBootGuard:
    def test_refuses_to_boot_in_production_without_a_key(self) -> None:
        from config import Settings

        with pytest.raises(ValueError, match="SECRETS_ENCRYPTION_KEY"):
            Settings(
                app_env="production", secrets_encryption_key="", strava_encryption_key=""
            )

    # 40 characters, decoding to 30 bytes: what `token_urlsafe(30)` and
    # `openssl rand -base64 30` produce. Plausible, and not a Fernet key.
    NOT_A_FERNET_KEY = "x" * 40

    def test_refuses_to_boot_on_a_key_fernet_cannot_use(self) -> None:
        from config import Settings

        with pytest.raises(ValueError, match="not a valid Fernet key"):
            Settings(app_env="production", secrets_encryption_key=self.NOT_A_FERNET_KEY)

    def test_a_broken_key_is_rejected_in_development_too(self) -> None:
        """Dev gets the same error: a key that cannot encrypt is never intended."""
        from config import Settings

        with pytest.raises(ValueError, match="not a valid Fernet key"):
            Settings(app_env="development", secrets_encryption_key=self.NOT_A_FERNET_KEY)

    def test_the_error_says_how_to_generate_a_correct_key(self) -> None:
        from config import Settings

        with pytest.raises(ValueError, match="Fernet.generate_key"):
            Settings(app_env="production", secrets_encryption_key=self.NOT_A_FERNET_KEY)

    def test_the_deprecated_name_is_validated_as_well(self) -> None:
        from config import Settings

        with pytest.raises(ValueError, match="not a valid Fernet key"):
            Settings(
                app_env="production",
                secrets_encryption_key="",
                strava_encryption_key=self.NOT_A_FERNET_KEY,
            )

    def test_a_real_fernet_key_still_boots(self) -> None:
        from config import Settings

        key = Fernet.generate_key().decode()
        assert Settings(app_env="production", secrets_encryption_key=key).encryption_key == key

    def test_allows_development_without_a_key(self) -> None:
        from config import Settings

        assert Settings(app_env="development", secrets_encryption_key="").app_env

    def test_accepts_production_with_a_key(self) -> None:
        from config import Settings

        settings = Settings(
            app_env="production", secrets_encryption_key=Fernet.generate_key().decode()
        )
        assert settings.is_dev_environment is False


class TestDeprecatedKeyName:
    """#613: dropping the old name outright would silently disable encryption."""

    def test_legacy_name_still_satisfies_the_production_guard(self) -> None:
        from config import Settings

        settings = Settings(
            app_env="production",
            secrets_encryption_key="",
            strava_encryption_key=Fernet.generate_key().decode(),
        )

        assert settings.encryption_key == settings.strava_encryption_key

    def test_legacy_name_still_encrypts(self, monkeypatch) -> None:
        from config import SecretPurpose, settings
        from models import EncryptedString

        monkeypatch.setattr(settings, "secrets_encryption_key", "")
        monkeypatch.setattr(
            settings, "strava_encryption_key", Fernet.generate_key().decode()
        )

        assert (
            EncryptedString(SecretPurpose.STRAVA)
            .process_bind_param("secret", None)
            .startswith("gAAAAA")
        )

    def test_using_the_legacy_name_is_reported(self, caplog) -> None:
        from config import Settings

        settings = Settings(
            app_env="production",
            secrets_encryption_key="",
            strava_encryption_key=Fernet.generate_key().decode(),
        )

        with caplog.at_level("WARNING"):
            settings.encryption_key

        assert "STRAVA_ENCRYPTION_KEY is deprecated" in caplog.text

    def test_current_name_wins_when_both_are_set(self) -> None:
        from config import Settings

        current = Fernet.generate_key().decode()
        settings = Settings(
            app_env="production",
            secrets_encryption_key=current,
            strava_encryption_key=Fernet.generate_key().decode(),
        )

        assert settings.encryption_key == current

    def test_compose_still_forwards_the_legacy_name(self) -> None:
        """An existing .env must not lose encryption on upgrade."""
        assert "STRAVA_ENCRYPTION_KEY:" in _backend_environment_block()

    def test_deployment_accepts_a_vault_that_kept_the_old_var(self) -> None:
        for line in ANSIBLE_ENV_TEMPLATE.read_text().splitlines():
            if line.startswith("SECRETS_ENCRYPTION_KEY="):
                assert "strava_encryption_key" in line
                return
        pytest.fail("SECRETS_ENCRYPTION_KEY is not rendered by app.env.j2")
