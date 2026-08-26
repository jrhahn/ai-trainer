"""Guards for the path a secret takes from the deploy templates into the app.

#612: ``STRAVA_ENCRYPTION_KEY`` was read correctly by ``Settings`` and used
correctly by ``EncryptedString``, and still every user API key in production was
stored as plaintext — because no line in ``compose.yml`` passed the variable
into the container, and nothing rendered it into the deployed ``.env``.  Python
tests could not see that, so these assertions read the deployment files.
"""

from pathlib import Path

import pytest
from cryptography.fernet import Fernet

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "compose.yml"
ANSIBLE_ENV_TEMPLATE = REPO_ROOT / "deploy" / "ansible" / "templates" / "app.env.j2"
ENV_EXAMPLE = REPO_ROOT / ".env.example"

# Settings the backend cannot be trusted to run without once it leaves dev.
SECURITY_CRITICAL_VARS = ("STRAVA_ENCRYPTION_KEY", "APP_ENV")


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
        if line.startswith("STRAVA_ENCRYPTION_KEY="):
            assert "default(" not in line, (
                "A default here means a deploy that forgot the secret silently "
                "stores plaintext instead of aborting."
            )
            return
    pytest.fail("STRAVA_ENCRYPTION_KEY is not rendered by app.env.j2")


def test_production_app_env_reaches_the_boot_guard() -> None:
    """The guard in Settings only fires when APP_ENV actually says production."""
    for line in ANSIBLE_ENV_TEMPLATE.read_text().splitlines():
        if line.startswith("APP_ENV="):
            assert "production" in line
            return
    pytest.fail("APP_ENV is not rendered by app.env.j2")


class TestEncryptedString:
    """The type itself: ciphertext when keyed, plaintext fallback when not."""

    def _column(self):
        from models import EncryptedString

        return EncryptedString()

    def test_stores_ciphertext_when_a_key_is_configured(self, monkeypatch) -> None:
        from config import settings

        key = Fernet.generate_key().decode()
        monkeypatch.setattr(settings, "strava_encryption_key", key)
        column = self._column()

        stored = column.process_bind_param("AIzaSy-not-a-real-key", None)

        assert stored != "AIzaSy-not-a-real-key"
        assert stored.startswith("gAAAAA"), "not a Fernet token"
        assert column.process_result_value(stored, None) == "AIzaSy-not-a-real-key"

    def test_stores_plaintext_without_a_key(self, monkeypatch) -> None:
        """Documents the dev-only behaviour that made #612 possible."""
        from config import settings

        monkeypatch.setattr(settings, "strava_encryption_key", "")

        assert self._column().process_bind_param("secret", None) == "secret"

    def test_reports_when_a_stored_secret_cannot_be_decrypted(
        self, monkeypatch, caplog
    ) -> None:
        from config import settings

        monkeypatch.setattr(
            settings, "strava_encryption_key", Fernet.generate_key().decode()
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

        with pytest.raises(ValueError, match="STRAVA_ENCRYPTION_KEY"):
            Settings(app_env="production", strava_encryption_key="")

    def test_allows_development_without_a_key(self) -> None:
        from config import Settings

        assert Settings(app_env="development", strava_encryption_key="").app_env

    def test_accepts_production_with_a_key(self) -> None:
        from config import Settings

        settings = Settings(
            app_env="production", strava_encryption_key=Fernet.generate_key().decode()
        )
        assert settings.is_dev_environment is False
