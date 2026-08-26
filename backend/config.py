"""Centralised application configuration.

All environment variables are declared here via Pydantic Settings.  Callers
import the module-level ``settings`` singleton instead of calling
``os.environ.get`` directly.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Canonical set of environment names treated as non-production ("dev"). Used to
# relax production-only requirements (strong JWT secret, at-rest encryption key).
# Shared with auth.py so both agree on what counts as a dev environment — a
# previous split definition meant APP_ENV=local/dev/testing was "dev" for the
# JWT check but "production" for the encryption-key check.
DEV_ENVS = frozenset({"development", "dev", "local", "test", "testing"})


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
    app_timezone: str = "Europe/Berlin"
    frontend_url: str = "http://localhost:5173"
    backend_url: str = "http://localhost:8000"
    server_url: str = ""
    activity_sync_interval_seconds: int = 1800
    duration_refresh_lookback_days: int = 21
    """How many days back the daily duration-refresh job re-checks moving_time.

    intervals.icu computes ``moving_time`` a few minutes after upload, so an
    early sync can store the wrong (elapsed) duration; the daily job re-derives
    ``duration_seconds`` from the current ``moving_time`` for rides in this
    window and recomputes the metrics chain (#429 Bug A)."""
    continuous_learning_enabled: bool = True
    """Run the athlete-learning step after every completed workout is imported (#388).

    When enabled, each freshly imported workout triggers a per-athlete learning
    pass (observations, contradictions, hypotheses, open questions) inline with
    activity sync. The weekly batch jobs remain as a backstop. Set to ``false``
    to fall back to weekly-only learning (e.g. to cap per-sync token spend)."""

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------
    database_url: str = "postgresql+asyncpg://localhost/aitrainer"

    # ------------------------------------------------------------------
    # Admin
    # ------------------------------------------------------------------
    admin_password: str = ""
    """Password for the /admin panel.  Leave empty to disable admin access."""

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
    authelia_proxy_secret_header: str = "X-Authelia-Proxy-Secret"
    """Header carrying the shared secret that the trusted reverse proxy injects."""
    authelia_proxy_shared_secret: str = ""
    """Secret the reverse proxy injects (and overwrites on inbound requests) to
    prove a request transited the proxy.  When set, the backend only trusts
    ``Remote-*`` headers on requests that carry the matching secret, so a request
    reaching the backend by any other path (direct container access, SSRF) cannot
    forge an Authelia identity.  Leave empty to rely solely on network isolation."""

    # ------------------------------------------------------------------
    # Strava
    # ------------------------------------------------------------------
    strava_client_id: str = ""
    strava_client_secret: str = ""

    # ------------------------------------------------------------------
    # Encryption at rest
    # ------------------------------------------------------------------
    secrets_encryption_key: str = ""
    """Fernet key encrypting every secret this app stores on a user's behalf.

    Covers Strava OAuth tokens, intervals.icu API keys and user-supplied AI
    provider keys.  Read it through :attr:`encryption_key`, never directly, so
    the deprecated name below keeps working.

    Generate with:
        python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

    Must be set in production (enforced below).  In development/test it may be
    omitted, in which case secrets are stored as plaintext.
    """

    strava_encryption_key: str = ""
    """Deprecated name for :attr:`secrets_encryption_key` (#613).

    It predates the key covering anything but Strava.  Still honoured so an
    existing deployment does not fall back to plaintext on upgrade — dropping it
    outright would silently disable encryption, which is the failure mode #612
    was about.
    """

    @property
    def encryption_key(self) -> str:
        """The active Fernet key, preferring the current name over the old one."""
        if self.secrets_encryption_key:
            return self.secrets_encryption_key
        if self.strava_encryption_key:
            logger.warning(
                "STRAVA_ENCRYPTION_KEY is deprecated and will be removed; "
                "rename it to SECRETS_ENCRYPTION_KEY. It encrypts intervals.icu "
                "and AI provider keys too, not only Strava tokens."
            )
            return self.strava_encryption_key
        return ""

    @property
    def is_dev_environment(self) -> bool:
        """Whether APP_ENV names a non-production (dev/test) environment."""
        return self.app_env.lower() in DEV_ENVS

    @model_validator(mode="after")
    def _require_encryption_key_in_production(self) -> "Settings":
        if not self.is_dev_environment and not self.encryption_key:
            raise ValueError(
                "SECRETS_ENCRYPTION_KEY must be set when APP_ENV is not 'development' or 'test'. "
                "Generate one with: "
                "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )
        return self

    # ------------------------------------------------------------------
    # AI providers
    # ------------------------------------------------------------------
    openai_api_key: str = ""
    gemini_api_key: str = ""
    allow_admin_ai_key_fallback: bool = True
    """When True (default), AI requests fall back to the backend owner's keys if
    the user has not configured their own.  Set to False to require every user to
    supply their own key (BYOK-only mode).
    """

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

    # Gemini defaults to Flash-Lite on every task (#511): $0.30/$2.50 per M tokens
    # against $1.50/$9.00 for Flash, for work that is overwhelmingly structured
    # JSON extraction under explicit instructions. Raise an individual task back
    # to "gemini-3.5-flash" via its env var if its output quality suffers — the
    # conversational coach is the one to watch.
    gemini_classify_model: str = "gemini-3.5-flash-lite"
    gemini_plan_model: str = "gemini-3.5-flash-lite"
    gemini_coach_model: str = "gemini-3.5-flash-lite"
    gemini_feedback_model: str = "gemini-3.5-flash-lite"

    # ------------------------------------------------------------------
    # Embeddings (cycling-science RAG)
    #
    # Separate from the chat models above: retrieval only works when the
    # corpus and the query are embedded by the same model, so a change here
    # means re-running scripts/ingest_cycling_science.py (#515).
    #
    # "gemini-embedding-001" is the GA model; "gemini-embedding-2" is its
    # successor and also emits 768 dimensions under Matryoshka truncation, so
    # it is a drop-in override. Both are pinned to a name that was verified
    # against the live model list — text-embedding-004 has been withdrawn.
    # ------------------------------------------------------------------
    # Write full prompts and responses to the log. Off by default and meant for
    # a deliberate debugging session only: prompts carry the athlete's health
    # data, which has no business sitting in a log file (#499/#516).
    log_llm_payloads: bool = False

    embedding_provider: str = "gemini"
    gemini_embedding_model: str = "gemini-embedding-001"
    openai_embedding_model: str = "text-embedding-3-small"

    # ------------------------------------------------------------------
    # Readiness recommendations
    #
    # How the coach's personal observations of the athlete (athlete-memory
    # facts) are matched to readiness recommendations:
    #   "llm"     – ask the LLM which recommendation each observation supports
    #               (nuanced, default; falls back to keyword on any failure)
    #   "keyword" – deterministic keyword/theme matching (no LLM call)
    # ------------------------------------------------------------------
    readiness_observation_matching: str = "llm"

    # ------------------------------------------------------------------
    # Computed helpers
    # ------------------------------------------------------------------

    @property
    def allowed_origins(self) -> list[str]:
        """Return CORS origins parsed from the (possibly comma-separated) FRONTEND_URL."""
        return [
            u.strip().rstrip("/") for u in self.frontend_url.split(",") if u.strip()
        ]

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
