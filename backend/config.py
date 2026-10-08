"""Centralised application configuration.

All environment variables are declared here via Pydantic Settings.  Callers
import the module-level ``settings`` singleton instead of calling
``os.environ.get`` directly.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from cryptography.fernet import Fernet
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Quoted in both key errors below: an operator who hits one should not have to go
# looking for how to produce a correct value.
_FERNET_KEYGEN = (
    'python -c "from cryptography.fernet import Fernet; '
    'print(Fernet.generate_key().decode())"'
)

logger = logging.getLogger(__name__)

# Canonical set of environment names treated as non-production ("dev"). Used to
# relax production-only requirements (strong JWT secret, at-rest encryption key).
# Shared with auth.py so both agree on what counts as a dev environment — a
# previous split definition meant APP_ENV=local/dev/testing was "dev" for the
# JWT check but "production" for the encryption-key check.
DEV_ENVS = frozenset({"development", "dev", "local", "test", "testing"})


class SecretPurpose:
    """What a stored secret is *for*, which decides which key encrypts it (#12).

    One Fernet key covered all of them, so rotating it meant re-encrypting
    everything at once — and the reason to rotate is usually a suspicion, which
    is the worst moment for an all-or-nothing operation on someone else's
    credentials.

    There are four and not the three the backlog named: ``totp_secret`` became an
    encrypted column with the second factor (#688), after that note was written.
    Rotating the shared key today would also lock every athlete out of their
    authenticator, which is the stated problem one type worse than stated.
    """

    STRAVA = "strava"
    INTERVALS = "intervals"
    AI_KEYS = "ai_keys"
    TOTP = "totp"

    ALL = (STRAVA, INTERVALS, AI_KEYS, TOTP)
    """Every purpose, for the sweeps that must not miss one."""


_DEDICATED_KEY_FIELDS = {
    SecretPurpose.STRAVA: "strava_token_encryption_key",
    SecretPurpose.INTERVALS: "intervals_encryption_key",
    SecretPurpose.AI_KEYS: "ai_key_encryption_key",
    SecretPurpose.TOTP: "totp_encryption_key",
}
"""Which setting holds each purpose's dedicated key.

A mapping rather than a naming convention, because the Strava one cannot follow
the convention — see the comment on the fields themselves.
"""


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
    """The fallback Fernet key, used by any purpose with no dedicated one.

    It encrypted every secret this app stores on a user's behalf until #12 —
    Strava OAuth tokens, intervals.icu API keys, the athlete's own AI provider
    keys and their TOTP secret — and still does for whichever of those has no key
    of its own. Read it through :attr:`encryption_key`, never directly, so the
    deprecated name below keeps working; read it through
    :meth:`encryption_keys_for` when encrypting, so the split applies.

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

    # --- per-purpose keys (#12) ---------------------------------------
    #
    # Each is optional. Absent means "use the shared key", so the split lands
    # one secret type at a time with no re-encryption pass and no deployment
    # that has to set all four at once.
    #
    # None of them is named STRAVA_ENCRYPTION_KEY, which is what the backlog
    # proposed. That name is taken: it is the pre-#613 alias for the *shared*
    # key, and reusing it would silently reinterpret any deployment still
    # setting it. Strava tokens would keep working, and intervals, AI and TOTP
    # secrets would fall back to SECRETS_ENCRYPTION_KEY — which such a
    # deployment has no reason to have set — and be written as plaintext. That
    # is #612's failure mode, reintroduced by a rename, and it is why the Strava
    # one carries TOKEN in its name.

    strava_token_encryption_key: str = ""
    """Dedicated key for ``strava_tokens.access_token`` / ``.refresh_token``."""

    intervals_encryption_key: str = ""
    """Dedicated key for ``intervals_tokens.api_key``."""

    ai_key_encryption_key: str = ""
    """Dedicated key for the athlete's own ``user_openai_api_key`` / ``user_gemini_api_key``."""

    totp_encryption_key: str = ""
    """Dedicated key for ``users.totp_secret`` (#688)."""

    @property
    def encryption_key(self) -> str:
        """The shared Fernet key, preferring the current name over the old one.

        Still the fallback for every purpose, and still required in production,
        because ciphertext written before the split is only readable with it.
        Retiring it needs a re-encryption pass, not a configuration change.
        """
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

    def _dedicated_key(self, purpose: str) -> str:
        """The key configured for *purpose* alone, or "" if there is none."""
        try:
            attribute = _DEDICATED_KEY_FIELDS[purpose]
        except KeyError:
            raise ValueError(f"Unknown secret purpose {purpose!r}") from None
        return getattr(self, attribute)

    def encryption_keys_for(self, purpose: str) -> list[str]:
        """Every key that may decrypt *purpose*, the one to encrypt with first.

        Order is the whole mechanism. ``MultiFernet`` encrypts with the first key
        and decrypts with any, so a dedicated key in front of the shared one
        means new writes move to it while rows written before the split stay
        readable — which is what lets this land one secret type at a time
        instead of behind a migration that re-encrypts four tables at once.

        Empty means store plaintext, which only happens in dev: production
        cannot boot without the shared key.
        """
        return [key for key in (self._dedicated_key(purpose), self.encryption_key) if key]

    @property
    def is_dev_environment(self) -> bool:
        """Whether APP_ENV names a non-production (dev/test) environment."""
        return self.app_env.lower() in DEV_ENVS

    @model_validator(mode="after")
    def _require_encryption_key_in_production(self) -> "Settings":
        if not self.is_dev_environment and not self.encryption_key:
            raise ValueError(
                "SECRETS_ENCRYPTION_KEY must be set when APP_ENV is not 'development' or 'test'. "
                "Generate one with: " + _FERNET_KEYGEN
            )
        return self

    @model_validator(mode="after")
    def _require_a_usable_encryption_key(self) -> "Settings":
        """Every key that is set must be one Fernet can actually use.

        Generate each with ``Fernet.generate_key()`` (see :data:`_FERNET_KEYGEN`)
        — 44 characters, url-safe base64, 32 bytes decoded. ``token_urlsafe`` and
        ``openssl rand`` produce the wrong length and are rejected here rather
        than on the first request that touches an encrypted column.

        The per-purpose keys are checked too, and by name (#12). A dedicated key
        that Fernet rejects would otherwise fail on first use — on exactly one
        column family, in a process that booted fine, while the other three
        purposes kept working. That is a worse bug than not starting.
        """
        # Derived from the one mapping rather than listed again here: a fifth
        # purpose added to _DEDICATED_KEY_FIELDS and forgotten in a second list
        # would skip validation entirely, which is the failure this check is for.
        candidates = [("SECRETS_ENCRYPTION_KEY", self.encryption_key)] + [
            (field.upper(), getattr(self, field))
            for field in _DEDICATED_KEY_FIELDS.values()
        ]
        for name, key in candidates:
            if not key:
                continue
            try:
                Fernet(key.encode())
            except (ValueError, TypeError) as exc:
                raise ValueError(
                    f"{name} is set but is not a valid Fernet key ({exc}). "
                    "It must be 32 bytes, url-safe base64-encoded — 44 characters. "
                    "Generate one with: " + _FERNET_KEYGEN
                ) from exc
        return self

    @model_validator(mode="after")
    def _refuse_the_stub_provider_outside_development(self) -> "Settings":
        """``AI_STUB_PROVIDER`` may only be on in a development environment.

        The stub answers every model call from its own arguments
        (``services/llm.StubProvider``). In a test harness that is the point; on
        a live instance it would mean athletes receiving placeholder text in the
        coach's voice, with a plan built from a loop rather than from their
        training — and nothing in the UI saying so. There is no amount of that
        which is acceptable, so it is a boot refusal rather than a warning.

        Refusing to start is the severe option and the right one here. The
        alternative is a process that comes up and serves canned coaching, which
        is the failure this check exists to make impossible; a deployment that
        will not start gets noticed in minutes.
        """
        if self.ai_stub_provider and not self.is_dev_environment:
            raise ValueError(
                "AI_STUB_PROVIDER is set but APP_ENV is "
                f"{self.app_env!r}. The stub answers every model call with "
                "canned text and must never run where an athlete can see it. "
                f"Set APP_ENV to one of {sorted(DEV_ENVS)}, or unset "
                "AI_STUB_PROVIDER."
            )
        return self

    @model_validator(mode="after")
    def _refuse_a_dedicated_key_that_is_also_the_shared_one(self) -> "Settings":
        """A dedicated key equal to the shared key is a split that did not happen.

        It validates, encrypts, decrypts and reads as done on the dashboard,
        while rotating either one still takes all four purposes with it. Copying
        the existing value into the new variable is the obvious wrong move, so it
        fails at boot rather than at the next incident (#12).
        """
        shared = self.encryption_key
        if not shared:
            return self
        for purpose in SecretPurpose.ALL:
            if self._dedicated_key(purpose) == shared:
                name = _DEDICATED_KEY_FIELDS[purpose].upper()
                raise ValueError(
                    f"{name} is set to the same value as SECRETS_ENCRYPTION_KEY, "
                    "which gives no independence at all — rotating either still "
                    "re-encrypts every secret type. Generate a separate key "
                    "with: " + _FERNET_KEYGEN
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

    On a deployment with open registration this default means any account that
    signs up spends the owner's money, which is half of #676.  It is left True
    so a single-user install keeps working out of the box, but a public
    deployment should set it explicitly — ``compose.yml`` now does.
    """

    ai_stub_provider: bool = False
    """Answer every model call from a stub instead of a provider
    (ai-trainer-ops#46).

    For the end-to-end suite, a scenario suite and CI judges: four separate
    pieces of work were each blocked on having no way to make the model answer
    deterministically, and none of them can carry a real key.

    Two locks, because the failure mode is an athlete being coached by a
    placeholder with nothing saying so. ``Settings`` refuses to construct with
    this on outside a development environment (see
    :meth:`_refuse_the_stub_provider_outside_development`), and
    ``llm.stub_is_active`` checks the environment again at the point of use, for
    the case where something has set the attribute on a live object.

    Default False, so it is on only where someone wrote it down.
    """

    # ------------------------------------------------------------------
    # AI spend controls (#676)
    #
    # Two independent limits, because they stop different things: the rate
    # limit stops a burst, the budget stops a slow drip that never trips it.
    # Both are per user and both apply only to ``api:`` sources — work an HTTP
    # request started.  Scheduler jobs are bounded by their schedule and
    # background tasks are bounded by the request that spawned them, so gating
    # the request gates the whole chain.
    # ------------------------------------------------------------------
    ai_rate_limit_enabled: bool = True
    ai_rate_limit_burst: int = 10
    """Requests per ``ai_rate_limit_burst_seconds`` before a 429."""
    ai_rate_limit_burst_seconds: int = 60
    ai_rate_limit_sustained: int = 100
    """Requests per ``ai_rate_limit_sustained_seconds`` before a 429.

    Generous for a human — an active athlete sends a few dozen coach messages a
    day — and immediately limiting for a script.
    """
    ai_rate_limit_sustained_seconds: int = 3600

    ai_token_budget: int = 0
    """Provider tokens one user may spend per ``ai_token_budget_window_days``.

    ``0`` disables the budget, which is the default so existing installs are
    unaffected by the upgrade.  A coach message costs ~16k tokens (#510/#556),
    so a budget is best set in millions: 5_000_000 is roughly 300 coach
    messages a month.
    """
    ai_token_budget_window_days: int = 30

    # ------------------------------------------------------------------
    # Auth brute-force controls (#682)
    #
    # The AI limits above protect the owner's money. These protect the
    # accounts, which is a different thing and was left unprotected: every
    # auth route was reachable at unlimited rate, and ``/admin/login`` is
    # publicly routed (``PathPrefix(/api)``, no proxy middleware) with a single
    # human-chosen password behind it.
    #
    # Keyed by email and by a global bucket rather than by client IP. The IP
    # arrives through Traefik (and, on the same-origin path, nginx) so trusting
    # it needs a trusted-proxy hop count this app does not establish — the same
    # reasoning routers/dependencies.py already records for the AI limit.
    # ------------------------------------------------------------------
    auth_rate_limit_enabled: bool = True

    login_rate_limit_attempts: int = 10
    """Login attempts per email per ``login_rate_limit_seconds``.

    Counts every attempt, not only failures: counting failures alone lets an
    attacker reset the window with one known-good credential.
    """
    login_rate_limit_seconds: int = 300

    login_rate_limit_global_attempts: int = 60
    """Login attempts across *all* emails per ``login_rate_limit_global_seconds``.

    The per-email window does nothing against spraying one password over many
    addresses, which is what a credential dump is used for. Deliberately
    generous: this bucket is shared, so a low value would let an attacker lock
    out the legitimate user. It bounds the spray rate, it does not stop it.
    """
    login_rate_limit_global_seconds: int = 60

    registration_rate_limit_attempts: int = 5
    """Registrations per ``registration_rate_limit_seconds``, globally.

    Global rather than per email, because an attacker picks a fresh address
    every time and a per-email bucket would never fill. Registration is public
    on this deployment (its own Traefik router), and every account that signs
    up can spend the owner's provider key unless ALLOW_ADMIN_AI_KEY_FALLBACK is
    off — so this is a spend control as much as an abuse control.
    """
    registration_rate_limit_seconds: int = 3600

    admin_login_rate_limit_attempts: int = 5
    """Admin password attempts per ``admin_login_rate_limit_seconds``, globally.

    One password guards the whole panel (every user's email, their token spend,
    and user deletion), so this is the tightest window in the app. Global
    because there is nothing to key on: the request carries a password and
    nothing else.
    """
    admin_login_rate_limit_seconds: int = 900

    # ------------------------------------------------------------------
    # Registration captcha (#686)
    #
    # Self-hosted proof-of-work, no third party and no CSP change — see the
    # module docstring in services/captcha.py for why not Turnstile. There is
    # deliberately no secret here: the signing key is derived from JWT_SECRET,
    # because every secret added lately needed threading through four places
    # and #617/#684 are both cases where one was missed.
    # ------------------------------------------------------------------
    captcha_enabled: bool = True

    captcha_max_number: int = 20_000
    """Upper bound on the secret the client brute-forces.

    Cost is linear and the average client tries half of it, so this is ~10k
    SHA-256 digests — a few hundred milliseconds in a browser, and invisible
    next to the round trip. It is not tuned to make solving *expensive*; the
    barrier is that solving requires executing the loop at all, which a bot
    POSTing the bare form does not do. Raising it punishes slow phones far more
    than it punishes an attacker who bothered to implement the solver.
    """

    captcha_ttl_seconds: int = 600
    """How long an issued challenge stays valid.

    Long enough to fill in a registration form unhurried, short enough that the
    replay guard's memory stays small.
    """

    captcha_challenge_rate_limit_attempts: int = 120
    """Challenges issued per ``captcha_challenge_rate_limit_seconds``, globally.

    Generous on purpose — one human may need several (reload, an expired
    solve, a password the strength check rejects) and each costs one HMAC.
    This bounds using the endpoint as a hashing service; registration's own
    limit is what bounds signup.
    """
    captcha_challenge_rate_limit_seconds: int = 300

    # ------------------------------------------------------------------
    # Second factor (#688)
    #
    # No signing secret here: the login challenge is HMAC'd with a key derived
    # from JWT_SECRET. #617, #684 and #694 were all a secret configured in one
    # of the four places it has to appear and silently defaulting in the rest;
    # a derived key cannot be half-configured.
    # ------------------------------------------------------------------
    totp_issuer: str = "Train Like A Pro"
    """Shown as the account name in the authenticator app."""

    totp_challenge_ttl_seconds: int = 300
    """How long the second step may lag the first.

    Long enough to fetch a phone, short enough that an intercepted challenge is
    not a standing invitation. It is single-use regardless.
    """

    totp_code_rate_limit_attempts: int = 5
    """Code submissions per account per ``totp_code_rate_limit_seconds``.

    Six digits is 10^6 and a step lasts 30 s with ±1 drift tolerance, so an
    unlimited endpoint is genuinely brute-forceable. This is the number that
    stops that; the single-use challenge is what stops one accepted password
    being worth an unbounded number of tries.
    """
    totp_code_rate_limit_seconds: int = 300

    trusted_device_days: int = 30
    """How long a device may skip the second factor after opting in."""

    admin_totp_secret: str = ""
    """Base32 TOTP secret for the admin panel. Empty disables the second factor.

    An environment variable rather than a stored, self-enrolled secret: the
    panel has no user row, and an enrollment endpoint would be one more
    unauthenticated surface in front of the account that can read every
    athlete's email address and delete any of them. Generate one with
    ``uv run python -m scripts.generate_admin_totp``.
    """

    # ------------------------------------------------------------------
    # Request limits (#682)
    # ------------------------------------------------------------------
    max_request_body_bytes: int = 64 * 1024 * 1024
    """Reject any request declaring a larger body, before it is read.

    A backstop, not the primary control: it is enforced from ``Content-Length``
    so a chunked request without one slips past. The routes that actually read
    a large body (the .fit uploads) cap themselves as they read, which is the
    guarantee that holds.
    """

    fit_upload_max_bytes: int = 10 * 1024 * 1024
    """Per-file cap on .fit uploads.

    A five-hour ride records ~1-2 MB, so this is several times the largest
    plausible real file and still small enough that a batch cannot exhaust
    memory. Enforced while reading, so an oversized file is never fully
    buffered.
    """

    fit_upload_bulk_max_files: int = 25
    """Files accepted in one bulk .fit upload.

    Each file costs an LLM call (``_analyse_fit_import``), so this bounds what
    one request can spend; the per-file rate-limit consume inside the route
    bounds it again against the user's normal AI allowance.
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
