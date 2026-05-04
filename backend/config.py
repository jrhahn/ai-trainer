"""Centralised application configuration.

All environment variables are declared here via Pydantic Settings.  Callers
import the module-level ``settings`` singleton instead of calling
``os.environ.get`` directly.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------
    app_env: str = "development"
    frontend_url: str = "http://localhost:5173"
    backend_url: str = "http://localhost:8000"
    server_url: str = ""

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------
    database_url: str = "postgresql+asyncpg://localhost/aitrainer"

    # ------------------------------------------------------------------
    # JWT / Auth
    # ------------------------------------------------------------------
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 10080  # 7 days

    # ------------------------------------------------------------------
    # Authelia
    # ------------------------------------------------------------------
    authelia_auth_enabled: bool = False
    authelia_remote_user_header: str = "Remote-User"
    authelia_remote_email_header: str = "Remote-Email"
    authelia_remote_name_header: str = "Remote-Name"
    authelia_internal_url: str = ""
    authelia_users_db_path: str = ""

    # ------------------------------------------------------------------
    # Strava
    # ------------------------------------------------------------------
    strava_client_id: str = ""
    strava_client_secret: str = ""
    strava_encryption_key: str = ""
    """Fernet key for encrypting Strava OAuth tokens at rest.

    Generate with:
        python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

    When empty, tokens are stored as plaintext (dev/test only).
    """

    # ------------------------------------------------------------------
    # AI providers
    # ------------------------------------------------------------------
    openai_api_key: str = ""
    gemini_api_key: str = ""

    # ------------------------------------------------------------------
    # AI model selection by task
    #
    # Each task type can be configured independently so cheap tasks use
    # fast/cheap models while conversational and feedback tasks can use
    # a stronger model when desired.
    #
    # classify  – question classification / routing (lightweight)
    # plan      – structured JSON plan generation / adaptation
    # coach     – conversational ask-trainer chat (prefer stronger model)
    # feedback  – post-ride / workout feedback (prefer stronger model)
    # ------------------------------------------------------------------
    openai_classify_model: str = "gpt-4o-mini"
    openai_plan_model: str = "gpt-4o-mini"
    openai_coach_model: str = "gpt-4o"
    openai_feedback_model: str = "gpt-4o"

    gemini_classify_model: str = "gemini-2.5-flash"
    gemini_plan_model: str = "gemini-2.5-flash"
    gemini_coach_model: str = "gemini-2.5-flash"
    gemini_feedback_model: str = "gemini-2.5-flash"

    # ------------------------------------------------------------------
    # Computed helpers
    # ------------------------------------------------------------------

    @property
    def allowed_origins(self) -> list[str]:
        """Return CORS origins parsed from the (possibly comma-separated) FRONTEND_URL."""
        return [u.strip().rstrip("/") for u in self.frontend_url.split(",") if u.strip()]

    @property
    def primary_frontend_url(self) -> str:
        origins = self.allowed_origins
        return origins[0] if origins else "http://localhost:5173"

    @property
    def effective_backend_url(self) -> str:
        """Return the public base URL for this server (used as Strava callback root)."""
        if self.server_url:
            url = self.server_url.rstrip("/")
            if "://" not in url:
                url = f"https://{url}"
            return url
        return self.backend_url.rstrip("/")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
