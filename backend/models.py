"""SQLAlchemy ORM models."""

import uuid
from datetime import datetime, timezone
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class EncryptedString(TypeDecorator):
    """Transparently encrypts/decrypts string values using Fernet symmetric encryption.

    When ``STRAVA_ENCRYPTION_KEY`` is not set the value is stored as plaintext,
    allowing dev/test environments to operate without a key while production
    always stores ciphertext.
    """

    impl = Text
    cache_ok = True

    def _get_fernet(self) -> Fernet | None:
        # Local import avoids circular dependency at module load.
        from config import settings

        key = settings.strava_encryption_key
        if not key:
            return None
        raw = key.encode() if isinstance(key, str) else key
        return Fernet(raw)

    def process_bind_param(self, value: str | None, dialect) -> str | None:
        if value is None:
            return value
        f = self._get_fernet()
        if f is None:
            return value
        return f.encrypt(value.encode()).decode()

    def process_result_value(self, value: str | None, dialect) -> str | None:
        if value is None:
            return value
        f = self._get_fernet()
        if f is None:
            return value
        try:
            return f.decrypt(value.encode()).decode()
        except (InvalidToken, Exception):
            # Graceful fallback for plaintext values stored before encryption was enabled.
            return value


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    name: Mapped[str | None] = mapped_column(String(255))
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    last_login: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # UserProfile fields
    bike_type: Mapped[str | None] = mapped_column(String(50))
    training_goal: Mapped[str | None] = mapped_column(String(50))
    race_date: Mapped[str | None] = mapped_column(String(20))
    race_description: Mapped[str | None] = mapped_column(Text)
    weekly_hours: Mapped[float | None] = mapped_column()
    follows_training_plan: Mapped[bool] = mapped_column(Boolean, default=False)
    resting_heart_rate: Mapped[int | None] = mapped_column(Integer)
    max_heart_rate: Mapped[int | None] = mapped_column(Integer)
    current_ftp: Mapped[int | None] = mapped_column(Integer)
    fitness_level: Mapped[str | None] = mapped_column(String(50))
    ai_provider: Mapped[str] = mapped_column(String(20), default="openai")
    consumed_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    # The same tokens split by how they bill. Input and output differ by ~6× and
    # cached input costs a tenth of input, so the total alone cannot be turned
    # into a cost. ``consumed_cached_tokens`` is a subset of the input count,
    # not a fourth bucket. Zero for everything consumed before #516.
    consumed_input_tokens: Mapped[int] = mapped_column(
        BigInteger, default=0, nullable=False, server_default="0"
    )
    consumed_output_tokens: Mapped[int] = mapped_column(
        BigInteger, default=0, nullable=False, server_default="0"
    )
    consumed_cached_tokens: Mapped[int] = mapped_column(
        BigInteger, default=0, nullable=False, server_default="0"
    )

    use_estimated_ftp: Mapped[bool] = mapped_column(Boolean, default=False)
    strava_auto_sync_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    strava_analysis_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    last_strava_activity_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    intervals_analysis_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    last_intervals_activity_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    intervals_auto_sync_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    is_onboarded: Mapped[bool] = mapped_column(Boolean, default=False)
    memory_updates_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    user_openai_api_key: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    user_gemini_api_key: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)

    # Relationships
    training_plan: Mapped["TrainingPlan | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    workout_logs: Mapped[list["WorkoutLog"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    race_events: Mapped[list["RaceEvent"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        order_by="RaceEvent.date",
    )
    chat_messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        order_by="ChatMessage.timestamp, ChatMessage.created_at, ChatMessage.id",
    )
    coach_memory: Mapped["CoachMemory | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    athlete_context: Mapped["AthleteContext | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    athlete_model: Mapped["AthleteModel | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    athlete_performance_model: Mapped["AthletePerformanceModel | None"] = (
        relationship(
            back_populates="user", uselist=False, cascade="all, delete-orphan"
        )
    )
    athlete_performance_snapshots: Mapped[
        list["AthletePerformanceSnapshot"]
    ] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        order_by="AthletePerformanceSnapshot.recorded_at",
    )
    athlete_memory_facts: Mapped[list["AthleteMemoryFact"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    athlete_hypotheses: Mapped[list["AthleteHypothesis"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    open_questions: Mapped[list["AthleteOpenQuestion"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    inquiries: Mapped[list["AthleteInquiry"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    validation_experiments: Mapped[list["AthleteExperiment"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    predictions: Mapped[list["AthletePrediction"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    availability_constraints: Mapped[list["AthleteAvailabilityConstraint"]] = (
        relationship(back_populates="user", cascade="all, delete-orphan")
    )
    home_location: Mapped["AthleteHomeLocation | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    strava_token: Mapped["StravaToken | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    intervals_token: Mapped["IntervalsToken | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    rider_assessment: Mapped["RiderAssessment | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    athlete_metric_snapshots: Mapped[list["AthleteMetricSnapshot"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        order_by="AthleteMetricSnapshot.recorded_at",
    )
    ride_metrics: Mapped[list["RideMetric"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        order_by="RideMetric.activity_date",
    )
    strava_import_jobs: Mapped[list["StravaImportJob"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        order_by="StravaImportJob.started_at",
    )


class TrainingPlan(Base):
    __tablename__ = "training_plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), unique=True, nullable=False
    )
    plan: Mapped[Any] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    user: Mapped["User"] = relationship(back_populates="training_plan")


class PlanDayHistory(Base):
    """Append-only log of every change to a single training-plan day.

    One row per changed day per plan write, capturing the day before and after
    and which trigger caused it (see ``services/plan_pipeline.PLAN_SOURCES``).
    The live plan stays in ``TrainingPlan.plan``; this table is for analytics and
    learning athlete behaviour and is never read on the hot path. An
    ``applied=False`` row records an automated change that was *blocked* by a
    user pin or a completed day — an "attempted correction" signal. See #343.
    """

    __tablename__ = "plan_day_history"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, index=True
    )
    date: Mapped[str] = mapped_column(String(10), nullable=False)
    # Which session on ``date`` this row describes (#496). A day may hold more
    # than one session (two-a-days), so ``(date, slot)`` — not date alone — is
    # what a history row is keyed to. Rows written before two-a-days existed, and
    # every single-session day, carry slot 0.
    slot: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # All per-day rows written by a single pipeline commit share one ``batch_id``
    # so a coach run (a plan generation, a nightly tune-up, one chat edit) can be
    # reconstituted from the log — for debugging and to collapse the run into a
    # single Coach Timeline card instead of one card per changed day. Nullable so
    # rows written before this column existed keep working. See #435.
    batch_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    old_day: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    new_day: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    applied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # One-line coach rationale for *this* day's change, backfilled after the run
    # by the narrator (services/coach_summary.py) for automated triggers. Null
    # for un-narrated triggers and for pre-#439 rows. Lives next to the diff it
    # explains so the per-day "why" has a single home — no separate store. The
    # run-level narrative lives in ``PlanChangeSummary`` keyed by ``batch_id``.
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    __table_args__ = (Index("ix_plan_day_history_user_date", "user_id", "date"),)


class PlanChangeSummary(Base):
    """One athlete-facing narrative per automated coach run (#439).

    Keyed by the ``batch_id`` shared by that run's ``PlanDayHistory`` rows, this
    holds the single plain-language summary of what the coach changed and why the
    new plan is better. It drives the one coach ``ChatMessage`` posted per run and
    lets the frontend suppress the redundant Coach-Timeline card for narrated
    runs. Only automated triggers (nightly maintenance, adapt, auto-adapt, ride
    review) are narrated; see ``services/coach_summary.NARRATED_SOURCES``.
    """

    __tablename__ = "plan_change_summary"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, index=True
    )
    batch_id: Mapped[str] = mapped_column(
        String(36), nullable=False, unique=True, index=True
    )
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class WorkoutLog(Base):
    __tablename__ = "workout_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False
    )
    date: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    # Which session on ``date`` this log belongs to (#496), so the morning gym
    # session and the evening ride each carry their own feedback instead of one
    # overwriting the other. Pre-two-a-day rows and single-session days are slot 0.
    slot: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    actual_duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    average_power: Mapped[int | None] = mapped_column(Integer)
    average_heart_rate: Mapped[int | None] = mapped_column(Integer)
    peak_power: Mapped[int | None] = mapped_column(Integer)
    perceived_effort: Mapped[int] = mapped_column(Integer, nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    completed_at: Mapped[str] = mapped_column(String(50), nullable=False)
    sport_type: Mapped[str] = mapped_column(
        String(50), default="cycling", nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="workout_logs")


class RaceEvent(Base):
    __tablename__ = "race_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, index=True
    )
    date: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    start_time: Mapped[str | None] = mapped_column(String(10), nullable=True)
    distance_km: Mapped[float] = mapped_column(Float, nullable=False)
    elevation_m: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    user: Mapped["User"] = relationship(back_populates="race_events")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    plan_update_count: Mapped[int | None] = mapped_column(Integer)
    # ISO dates of availability constraints this assistant reply reported as
    # blocking a coach-requested change. Lets a follow-up "lift that constraint"
    # resolve "that" to the constraint the last override note flagged (#437).
    flagged_constraint_dates: Mapped[Any | None] = mapped_column(JSON, nullable=True)

    user: Mapped["User"] = relationship(back_populates="chat_messages")


class CoachMemory(Base):
    __tablename__ = "coach_memory"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), primary_key=True
    )
    memory: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    user: Mapped["User"] = relationship(back_populates="coach_memory")


class AthleteContext(Base):
    __tablename__ = "athlete_context"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), primary_key=True
    )
    training_tendency: Mapped[str] = mapped_column(
        String(30), default="unknown", nullable=False
    )
    rest_response: Mapped[str] = mapped_column(
        String(30), default="unknown", nullable=False
    )
    motivation_drivers: Mapped[Any] = mapped_column(JSON, default=list, nullable=False)
    adherence_pattern: Mapped[str] = mapped_column(
        String(30), default="unknown", nullable=False
    )
    strengths: Mapped[Any] = mapped_column(JSON, default=list, nullable=False)
    weaknesses: Mapped[Any] = mapped_column(JSON, default=list, nullable=False)
    preferred_terrain: Mapped[Any] = mapped_column(JSON, default=list, nullable=False)
    preferred_session_types: Mapped[Any] = mapped_column(
        JSON, default=list, nullable=False
    )
    coaching_risks: Mapped[Any] = mapped_column(JSON, default=list, nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    user: Mapped["User"] = relationship(back_populates="athlete_context")


class AthleteModel(Base):
    """The long-term, structured model of an athlete's durable capabilities (#384).

    Distinct from :class:`AthleteContext` (behavioural/coaching tendencies) and
    from per-session ride data: this captures the athlete's *physiological and
    performance profile* — the qualities that change slowly over months, such as
    threshold power, VO2 max, how well they hold threshold, how quickly they
    recover, and how they tolerate heat. The coach derives and refreshes it from
    accumulated training history (see ``services.insight_generation``) and the
    athlete can review and correct it. It is injected into coaching prompts as
    stable knowledge about who the athlete is, not what they did yesterday.
    """

    __tablename__ = "athlete_model"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), primary_key=True
    )
    # Quantitative capacity anchors (nullable until known).
    ftp_watts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vo2max: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Qualitative capability assessments, short free-text descriptors
    # (e.g. "strong", "fades after 20 min", "handles heat well").
    pacing_quality: Mapped[str] = mapped_column(Text, default="", nullable=False)
    recovery_ability: Mapped[str] = mapped_column(Text, default="", nullable=False)
    threshold_durability: Mapped[str] = mapped_column(
        Text, default="", nullable=False
    )
    heat_tolerance: Mapped[str] = mapped_column(Text, default="", nullable=False)
    preferred_training_style: Mapped[str] = mapped_column(
        Text, default="", nullable=False
    )
    strengths: Mapped[Any] = mapped_column(JSON, default=list, nullable=False)
    weaknesses: Mapped[Any] = mapped_column(JSON, default=list, nullable=False)
    risk_factors: Mapped[Any] = mapped_column(JSON, default=list, nullable=False)
    # Short narrative overview and the coach's confidence in the current model.
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="athlete_model")


class AthleteMemoryFact(Base):
    """A durable piece of athlete knowledge the coach relies on.

    Carries a ``kind`` discriminator (#386) separating stable **facts** (values
    stated or measured about the athlete) from **observations** (patterns of
    repeated behaviour inferred from training history). Both share one lifecycle
    — confidence accrual, decay, contradiction and athlete validation — which is
    why they live in a single table rather than two. The third knowledge type,
    hypotheses that still need validation, is :class:`AthleteHypothesis`.
    """

    __tablename__ = "athlete_memory_facts"
    __table_args__ = (
        Index(
            "ix_athlete_memory_facts_user_category_key",
            "user_id",
            "category",
            "fact_key",
            unique=True,
        ),
        Index("ix_athlete_memory_facts_user_status", "user_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, index=True
    )
    fact: Mapped[str] = mapped_column(Text, nullable=False)
    fact_key: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(
        String(50), default="general", nullable=False
    )
    # Which knowledge type this row is (#386): a stable ``fact`` stated or
    # measured about the athlete (FTP, max HR, weight) versus an ``observation``
    # of repeated behaviour the coach inferred from training history (prefers
    # MTB, fades late in intervals). Hypotheses — claims that still need
    # validation — live in their own :class:`AthleteHypothesis` table.
    kind: Mapped[str] = mapped_column(
        String(20), default="observation", nullable=False
    )
    source_snippet: Mapped[str] = mapped_column(Text, default="", nullable=False)
    source_exchange_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    last_confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    confidence: Mapped[float] = mapped_column(Float, default=0.35, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    # Set when fresh training evidence contradicts this fact: a human-readable
    # reason surfaced to the athlete so they can validate or correct the fact.
    contradiction_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    observation_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="athlete_memory_facts")


class AthleteHypothesis(Base):
    """A speculative, testable claim the coach forms about an athlete.

    Distinct from :class:`AthleteMemoryFact`: a memory fact is something observed
    or stated and trusted enough to inform coaching, whereas a hypothesis is a
    tentative causal/predictive idea — e.g. "upper-body strength training
    suppresses heart-rate response the following day" — that still ``needs
    validation``. It accumulates supporting evidence over time and, once the
    athlete confirms it, is promoted into a memory fact so it can inform advice.
    """

    __tablename__ = "athlete_hypotheses"
    __table_args__ = (
        Index(
            "ix_athlete_hypotheses_user_category_key",
            "user_id",
            "category",
            "statement_key",
            unique=True,
        ),
        Index("ix_athlete_hypotheses_user_status", "user_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, index=True
    )
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    statement_key: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(
        String(50), default="general", nullable=False
    )
    # Human-readable summary of the evidence that motivates the hypothesis.
    rationale: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # Structured supporting evidence (list of concrete observations) and the
    # competing explanations the coach must still rule out (#479). Deterministic
    # performance-model hypotheses populate these; older LLM-formed hypotheses
    # leave them empty, so both are nullable.
    evidence: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    alternative_explanations: Mapped[list[Any] | None] = mapped_column(
        JSON, nullable=True
    )
    confidence: Mapped[float] = mapped_column(Float, default=0.35, nullable=False)
    # How many supporting observations back the hypothesis (the "Evidence" count).
    evidence_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # proposed (needs validation) -> confirmed | refuted.
    status: Mapped[str] = mapped_column(
        String(20), default="proposed", nullable=False
    )
    first_proposed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="athlete_hypotheses")


class AthleteOpenQuestion(Base):
    """An unanswered question the coach explicitly tracks about an athlete (#385).

    Where an :class:`AthleteHypothesis` is a tentative *answer* the coach proposes
    ("strength training suppresses HR response"), an open question is the coach
    admitting what it does **not** yet know — a first-class, athlete-visible list
    of the uncertainties it is actively trying to resolve ("Is FTP
    underestimated?"). Each question records the ``evidence`` currently pointing
    at it and what it still ``needs`` to be answered (e.g. a 30-minute threshold
    test). The coach derives questions from training history and accrues evidence
    when the same question recurs; once enough evidence exists the question
    auto-closes to ``answered`` with a short ``resolution``, so the list stays a
    live picture of open uncertainty rather than a growing pile.
    """

    __tablename__ = "athlete_open_questions"
    __table_args__ = (
        Index(
            "ix_athlete_open_questions_user_category_key",
            "user_id",
            "category",
            "question_key",
            unique=True,
        ),
        Index("ix_athlete_open_questions_user_status", "user_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, index=True
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    question_key: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(
        String(50), default="general", nullable=False
    )
    # The evidence currently on file that bears on the question (the "Evidence"
    # block in the athlete-facing list).
    evidence: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # What is still needed to answer it — a test, a comparison, or more
    # observations (the "Needs" block).
    needs: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # How many independent observations back the question; drives auto-close.
    evidence_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # open (still unanswered) -> answered | dismissed.
    status: Mapped[str] = mapped_column(String(20), default="open", nullable=False)
    # The short answer recorded when the question closes, if any.
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_asked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="open_questions")


class AthleteInquiry(Base):
    """A question the coach puts to the athlete because data can never answer it (#506).

    The coach's other uncertainty records all resolve themselves by *waiting*: an
    :class:`AthleteOpenQuestion` accrues evidence until the training history settles
    it, and an :class:`AthleteExperiment` asks the athlete to produce that evidence
    with a test. An inquiry is the residual — the things no future ride will ever
    reveal, because they live in the athlete's head or their week rather than in
    their power file: why a session was skipped, whether a niggle is still there,
    what they actually want from the season, whether the power meter got swapped.

    Generation is therefore gated on *inferability*: the coach first reasons about
    what the incoming data stream will tell it, and only asks about what is left
    over. ``why_asking`` records that justification in the coach's own words, and
    ``settings_hint`` names the place the athlete can state the answer themselves.

    A pending inquiry is pinned in the chat until it is dealt with. An answer that
    does not actually answer the question buys one kind rephrasing (``ask_count``
    tops out at :data:`ATHLETE_INQUIRY_MAX_ASKS`); if the second reply still misses,
    the inquiry moves to ``needs_settings`` and the coach stops asking rather than
    nagging.
    """

    __tablename__ = "athlete_inquiries"
    __table_args__ = (
        Index(
            "ix_athlete_inquiries_user_category_key",
            "user_id",
            "category",
            "question_key",
            unique=True,
        ),
        Index("ix_athlete_inquiries_user_status", "user_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, index=True
    )
    # The question as currently put to the athlete. Rewritten in place when the
    # coach rephrases after an answer that missed.
    question: Mapped[str] = mapped_column(Text, nullable=False)
    # Dedupe key derived from the *original* phrasing, so a rephrasing does not
    # let the same inquiry be raised twice.
    question_key: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(
        String(50), default="general", nullable=False
    )
    # Why the coach cannot work this out on its own — shown to the athlete so the
    # question reads as reasoning rather than interrogation.
    why_asking: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # Where in Settings the athlete can state or change this themselves; used for
    # the hand-off once the coach gives up asking.
    settings_hint: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # pending (awaiting the athlete) -> answered | needs_settings | dismissed.
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    # The athlete's most recent reply, kept even when it did not resolve the
    # question — a partial answer still beats nothing.
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    # How many times this has been put to the athlete (1 = first ask).
    ask_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # The coach's note when a reply missed: the rephrasing hint, or the hand-off
    # to Settings once asking stops.
    follow_up_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    asked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    answered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="inquiries")


class AthleteExperiment(Base):
    """A concrete validation experiment the coach proposes to resolve uncertainty.

    When a question about an athlete stays unsettled — most often because an
    :class:`AthleteHypothesis` still ``needs validation`` — the coach proposes a
    small, repeatable experiment the athlete can actually run rather than guessing
    (e.g. "compare both bikes using identical power pedals" or "repeat the VO2
    session with shorter recoveries"). Each experiment records the open question,
    the protocol to run, and what a result would tell the coach; completing or
    dismissing it resolves the suggestion.
    """

    __tablename__ = "validation_experiments"
    __table_args__ = (
        Index(
            "ix_validation_experiments_user_key",
            "user_id",
            "protocol_key",
            unique=True,
        ),
        Index("ix_validation_experiments_user_status", "user_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, index=True
    )
    # Soft reference to the hypothesis this experiment aims to validate, if any.
    # Kept as a plain column (not a hard FK) so resolving or deleting a hypothesis
    # never orphans a still-useful experiment.
    hypothesis_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # The open question / uncertainty the experiment is designed to settle.
    question: Mapped[str] = mapped_column(Text, nullable=False)
    # The concrete protocol the athlete should run.
    protocol: Mapped[str] = mapped_column(Text, nullable=False)
    protocol_key: Mapped[str] = mapped_column(String(255), nullable=False)
    # What a result would tell the coach (expected signal / decision rule).
    rationale: Mapped[str] = mapped_column(Text, default="", nullable=False)
    category: Mapped[str] = mapped_column(
        String(50), default="general", nullable=False
    )
    # suggested (awaiting the athlete) -> completed | dismissed.
    status: Mapped[str] = mapped_column(
        String(20), default="suggested", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="validation_experiments")


class AthletePrediction(Base):
    """A forward-looking, checkable claim the coach makes about an athlete.

    #383: to measure coaching quality the coach records each prediction it makes
    ("the athlete should be fully recovered tomorrow") alongside the concrete
    ``expected_outcome`` that would confirm it. Once enough time passes the
    prediction is evaluated against what actually happened (``actual_outcome``):
    a correct call nudges its ``confidence`` up, a wrong one reduces it, and the
    running hit-rate across all evaluated predictions is the coach's measured
    accuracy — the closing of the loop that keeps the coach honest.
    """

    __tablename__ = "athlete_predictions"
    __table_args__ = (
        Index(
            "ix_athlete_predictions_user_key",
            "user_id",
            "prediction_key",
            unique=True,
        ),
        Index("ix_athlete_predictions_user_status", "user_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, index=True
    )
    # The coach's claim, e.g. "the athlete should be fully recovered tomorrow".
    prediction: Mapped[str] = mapped_column(Text, nullable=False)
    prediction_key: Mapped[str] = mapped_column(String(255), nullable=False)
    # The observable result that would confirm the prediction (how to check it).
    expected_outcome: Mapped[str] = mapped_column(Text, nullable=False)
    # What actually happened, filled in when the prediction is evaluated.
    actual_outcome: Mapped[str | None] = mapped_column(Text, nullable=True)
    # When the prediction can be checked, e.g. "tomorrow" or "next week" — free
    # text kept for the athlete and to help the evaluator judge if it is due yet.
    horizon: Mapped[str] = mapped_column(Text, default="", nullable=False)
    category: Mapped[str] = mapped_column(
        String(50), default="general", nullable=False
    )
    # The coach's confidence in the prediction; rises on a correct call and falls
    # on a wrong one when the prediction is evaluated.
    confidence: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    # pending (awaiting outcome) -> correct | incorrect.
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    evaluated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="predictions")


class AthleteAvailabilityConstraint(Base):
    __tablename__ = "athlete_availability_constraints"
    __table_args__ = (
        Index(
            "ix_athlete_availability_constraints_user_active",
            "user_id",
            "active",
        ),
        Index(
            "ix_athlete_availability_constraints_user_date",
            "user_id",
            "constraint_date",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False
    )
    constraint_type: Mapped[str] = mapped_column(
        String(30), default="no_training", nullable=False
    )
    constraint_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    weekday: Mapped[str | None] = mapped_column(String(10), nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    source: Mapped[str] = mapped_column(Text, default="", nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    expires_on: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # For positive ("required_workout") constraints: the session that must be
    # present on the constrained day, e.g. {"workoutType": "endurance",
    # "minDurationMinutes": 120}. Null for negative ("no_training") constraints.
    required_workout: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="availability_constraints")


class AthleteHomeLocation(Base):
    """Where the athlete usually trains — the anchor for every weather lookup (#495).

    Previously the training location was re-derived on every request from the
    single latest ride that happened to carry GPS, so one holiday ride moved the
    whole forecast. This persists it as a first-class athlete attribute, seeded by
    clustering typical ride start points (:mod:`services.home_location`) and
    overridable by the athlete through the coach ("I mostly train near X now").

    ``source`` is the authority marker that keeps the two writers apart:
    ``user_set`` beats ``inferred``, and the inference pass must never overwrite a
    ``user_set`` row — the stale-snapshot clobber class the plan pipeline already
    guards against (#342/#345/#346). ``confidence`` and ``ride_count`` record how
    much history backed an inferred cluster so the coach can be honest about it.
    """

    __tablename__ = "athlete_home_location"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), primary_key=True
    )
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    # Human-readable place name ("Freiburg"); empty for a purely inferred centroid.
    label: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    # "inferred" (clustered ride starts) | "user_set" (athlete told the coach).
    source: Mapped[str] = mapped_column(
        String(20), default="inferred", nullable=False
    )
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    # How many ride starts fell inside the cluster this location came from.
    ride_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="home_location")


class StravaToken(Base):
    __tablename__ = "strava_tokens"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), primary_key=True
    )
    access_token: Mapped[str] = mapped_column(EncryptedString, nullable=False)
    refresh_token: Mapped[str] = mapped_column(EncryptedString, nullable=False)
    expires_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    athlete_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    athlete_name: Mapped[str] = mapped_column(String(255), default="")

    user: Mapped["User"] = relationship(back_populates="strava_token")


class IntervalsToken(Base):
    __tablename__ = "intervals_tokens"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), primary_key=True
    )
    api_key: Mapped[str] = mapped_column(EncryptedString, nullable=False)
    athlete_id: Mapped[str] = mapped_column(String(64), default="0", nullable=False)
    athlete_name: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="intervals_token")


class StravaImportJob(Base):
    __tablename__ = "strava_import_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="running", nullable=False)
    total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    processed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    imported: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    skipped: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_activities: Mapped[Any] = mapped_column(JSON, default=list, nullable=False)
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped["User"] = relationship(back_populates="strava_import_jobs")


class RiderAssessment(Base):
    __tablename__ = "rider_assessments"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), primary_key=True
    )
    estimated_ftp: Mapped[int | None] = mapped_column(Integer)
    rider_type: Mapped[str] = mapped_column(String(50), nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    hr_zones: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    ride_insights: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_ride_feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    login_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The dashboard's training-status chip, written by the coach rather than by
    # the browser (#499), so the label and the coach's explanation of it can
    # never disagree. Cleared by ``services.status_pipeline`` when the plan or
    # the athlete's activity changes, then lazily regenerated on the next load.
    training_status_label: Mapped[str | None] = mapped_column(String(60), nullable=True)
    training_status_tone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    training_status_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    user: Mapped["User"] = relationship(back_populates="rider_assessment")


class AthleteMetricSnapshot(Base):
    """Time-series snapshot of key athlete performance metrics.

    A new row is inserted each time a Strava analysis produces updated
    estimates so the athlete can view their progression over time.
    """

    __tablename__ = "athlete_metric_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, index=True
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    ftp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Best 5-minute mean power at the time of the snapshot — the maximal aerobic
    # power proxy that FTP is sanity-checked against (FTP must sit below it).
    map_5min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ctl: Mapped[float | None] = mapped_column(nullable=True)
    atl: Mapped[float | None] = mapped_column(nullable=True)
    tsb: Mapped[float | None] = mapped_column(nullable=True)
    source: Mapped[str] = mapped_column(String(50), default="strava_analysis")

    user: Mapped["User"] = relationship(back_populates="athlete_metric_snapshots")


class AthletePerformanceModel(Base):
    """Deterministic, per-attribute physiological model of the athlete (#475).

    Distinct from :class:`AthleteModel` (#384), which is an LLM-derived, qualitative
    free-text profile with a single overall confidence. This model is the data
    layer for the Athlete Performance Model epic (#474): a rule-based, quantitative
    picture inferred across many workouts, where **every** attribute carries its own
    estimate/score, confidence, evidence and missing information.

    Attributes are stored as structured JSON keyed by attribute name (``vo2max``,
    ``ftp``, ``map``, ``fractional_utilization``, ``aerobic_endurance``,
    ``fatigue_resistance``, ``anaerobic_capacity``, …) so the set can grow without
    schema churn. Each value is an ``AthletePerformanceAttribute``-shaped dict::

        {"estimate": 302, "score": null, "confidence": 0.74, "unit": "W",
         "evidence": [...], "missing_information": [...]}

    The inference engine (#476) writes ``attributes``; limiter detection (#477)
    writes ``likely_limiter`` and the ranked ``limiters`` list.
    """

    __tablename__ = "athlete_performance_models"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), primary_key=True
    )
    # Structured per-attribute inferences, keyed by attribute name.
    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    # Most probable physiological limiter (set by #477; None when undetermined).
    likely_limiter: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Confidence-ranked candidate limiters (#477), each a dict with
    # ``limiter``/``confidence``/``evidence``/``counter_evidence``.
    limiters: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    # Provenance: rolling window (days) and ride count the inference derived from.
    source_window_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    derived_from_rides: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="athlete_performance_model")


class AthletePerformanceSnapshot(Base):
    """Time-series snapshot of the Athlete Performance Model (#475).

    A new row is written each time the inference engine recomputes the model so
    trends over months (e.g. "development over the last months" for Level 2) stay
    queryable. Mirrors :class:`AthleteMetricSnapshot`.
    """

    __tablename__ = "athlete_performance_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, index=True
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    likely_limiter: Mapped[str | None] = mapped_column(String(50), nullable=True)
    limiters: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)

    user: Mapped["User"] = relationship(
        back_populates="athlete_performance_snapshots"
    )


class RideMetric(Base):
    """Per-ride time-series record with pre-computed training metrics.

    One row per Strava activity per user.  Serves as the structured context
    fed to every LLM call so the AI never needs to re-process raw streams.
    CTL/ATL/TSB here are derived from actual ride TSS (not plan estimates).
    """

    __tablename__ = "ride_metrics"
    __table_args__ = (
        Index(
            "ix_ride_metrics_user_activity",
            "user_id",
            "strava_activity_id",
            unique=True,
        ),
        Index(
            "ix_ride_metrics_user_source_external",
            "user_id",
            "activity_source",
            "external_activity_id",
            unique=True,
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, index=True
    )
    strava_activity_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    activity_source: Mapped[str] = mapped_column(
        String(50), default="strava", nullable=False
    )
    external_activity_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    activity_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    activity_start_datetime: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )
    activity_date: Mapped[str] = mapped_column(String(20), nullable=False)
    sport_type: Mapped[str] = mapped_column(
        String(50), default="cycling", nullable=False
    )
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    start_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    start_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    weather_temperature_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    weather_apparent_temperature_c: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    weather_condition: Mapped[str | None] = mapped_column(String(50), nullable=True)
    weather_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    weather_wind_speed_kph: Mapped[float | None] = mapped_column(Float, nullable=True)
    weather_precipitation_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    weather_source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    avg_power_w: Mapped[int | None] = mapped_column(Integer, nullable=True)
    normalized_power_w: Mapped[int | None] = mapped_column(Integer, nullable=True)
    intensity_factor: Mapped[float | None] = mapped_column(nullable=True)
    tss: Mapped[float | None] = mapped_column(nullable=True)
    ftp_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ctl_after: Mapped[float | None] = mapped_column(nullable=True)
    atl_after: Mapped[float | None] = mapped_column(nullable=True)
    tsb_after: Mapped[float | None] = mapped_column(nullable=True)
    ride_purpose: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # When the provider answered 404 for this row's external_activity_id. The
    # id is then permanently dead — rows imported before the id-precision fix
    # (#427) carry float64-corrupted ids whose originals are unrecoverable — so
    # every backfill that would re-fetch the activity skips the row instead of
    # asking again on the next tick (#517). NULL means "never asked, or the
    # last answer was not a 404".
    provider_unfetchable_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    classification_confidence: Mapped[str | None] = mapped_column(
        String(10), nullable=True
    )
    classification_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Compact per-ride physiological signals (power-duration envelope, HR drift,
    # first/second-half power & HR) derived from the stream at analysis time and
    # persisted so the cross-workout inference engine (#476) can aggregate them
    # without re-fetching streams. NULL when no usable stream was available.
    perf_signals: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    coach_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Quick subjective leg-freshness the athlete taps on the dashboard
    # ("fresh"/"normal"/"heavy"); NULL means not set and is ignored everywhere.
    feel_legs: Mapped[str | None] = mapped_column(String(10), nullable=True)
    label_override: Mapped[str | None] = mapped_column(String(50), nullable=True)
    coach_reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    plan_match_status: Mapped[str] = mapped_column(
        String(20), default="unmatched", nullable=False
    )
    matched_plan_date: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Which session on ``matched_plan_date`` this ride was matched to (#496).
    # A date can hold several planned sessions, so the morning gym activity and
    # the evening ride each point at their own slot instead of competing for the
    # one day. NULL for unmatched rides and for pre-two-a-day rows (slot 0).
    matched_plan_slot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    matched_plan_snapshot: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    matched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    user: Mapped["User"] = relationship(back_populates="ride_metrics")


class LlmCall(Base):
    """One provider call, kept after the container that made it is gone (#549).

    #516 already emitted a structured line per call. Container logs die with the
    container and every deploy recreates it, so the record covered "since the
    last deploy" — hours on an active day, which is not enough to answer whether
    a prompt change moved the bill (#538) or whether the spend actually came
    down (#510).

    Written from the collection scope in ``services.token_accounting``, so a
    call is recorded exactly where its tokens are already counted. The log line
    stays: it is what you read while something is going wrong, this is what you
    query afterwards.
    """

    __tablename__ = "llm_calls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # Nullable on purpose. A call made outside every scope belongs to nobody —
    # that is the bug #537 fixed — and dropping the row would hide exactly the
    # calls worth finding.
    user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    task: Mapped[str] = mapped_column(String(30), nullable=False)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    model: Mapped[str] = mapped_column(String(80), nullable=False)
    source: Mapped[str] = mapped_column(String(60), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # A subset of input_tokens, not a fourth bucket — both providers report the
    # cache hit as part of the prompt count.
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    json_mode: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ok: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    error: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Hash of the system prompt. The reason this is a column and not a log
    # field: a cost jump is explained by joining spend to the prompt that
    # produced it, which needs both to still exist.
    prompt_sha: Mapped[str] = mapped_column(String(12), nullable=False)

    __table_args__ = (
        # Every question asked of this table is "over some period", usually
        # narrowed to one source or model.
        Index("ix_llm_calls_created_at", "created_at"),
        Index("ix_llm_calls_source_created_at", "source", "created_at"),
    )
