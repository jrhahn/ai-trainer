"""SQLAlchemy ORM models."""

import uuid
from datetime import datetime, timezone
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
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
        from config import settings  # local import avoids circular dependency at module load

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
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(255))
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

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

    use_estimated_ftp: Mapped[bool] = mapped_column(Boolean, default=False)
    strava_analysis_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    last_strava_activity_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    is_onboarded: Mapped[bool] = mapped_column(Boolean, default=False)

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
        back_populates="user", cascade="all, delete-orphan", order_by="ChatMessage.timestamp"
    )
    coach_memory: Mapped["CoachMemory | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    strava_token: Mapped["StravaToken | None"] = relationship(
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    user: Mapped["User"] = relationship(back_populates="training_plan")


class WorkoutLog(Base):
    __tablename__ = "workout_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    date: Mapped[str] = mapped_column(String(20), nullable=False, index=True)

    actual_duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    average_power: Mapped[int | None] = mapped_column(Integer)
    average_heart_rate: Mapped[int | None] = mapped_column(Integer)
    peak_power: Mapped[int | None] = mapped_column(Integer)
    perceived_effort: Mapped[int] = mapped_column(Integer, nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    completed_at: Mapped[str] = mapped_column(String(50), nullable=False)
    sport_type: Mapped[str] = mapped_column(String(50), default="cycling", nullable=False)

    user: Mapped["User"] = relationship(back_populates="workout_logs")


class RaceEvent(Base):
    __tablename__ = "race_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    date: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    start_time: Mapped[str | None] = mapped_column(String(10), nullable=True)
    distance_km: Mapped[float] = mapped_column(Float, nullable=False)
    elevation_m: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    user: Mapped["User"] = relationship(back_populates="race_events")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[str] = mapped_column(String(50), nullable=False)
    plan_update_count: Mapped[int | None] = mapped_column(Integer)

    user: Mapped["User"] = relationship(back_populates="chat_messages")


class CoachMemory(Base):
    __tablename__ = "coach_memory"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), primary_key=True
    )
    memory: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    user: Mapped["User"] = relationship(back_populates="coach_memory")


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


class StravaImportJob(Base):
    __tablename__ = "strava_import_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), default="running", nullable=False)
    total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    processed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    imported: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    skipped: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_activities: Mapped[Any] = mapped_column(JSON, default=list, nullable=False)
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

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
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    user: Mapped["User"] = relationship(back_populates="rider_assessment")


class AthleteMetricSnapshot(Base):
    """Time-series snapshot of key athlete performance metrics.

    A new row is inserted each time a Strava analysis produces updated
    estimates so the athlete can view their progression over time.
    """

    __tablename__ = "athlete_metric_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    ftp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ctl: Mapped[float | None] = mapped_column(nullable=True)
    atl: Mapped[float | None] = mapped_column(nullable=True)
    tsb: Mapped[float | None] = mapped_column(nullable=True)
    source: Mapped[str] = mapped_column(String(50), default="strava_analysis")

    user: Mapped["User"] = relationship(back_populates="athlete_metric_snapshots")


class RideMetric(Base):
    """Per-ride time-series record with pre-computed training metrics.

    One row per Strava activity per user.  Serves as the structured context
    fed to every LLM call so the AI never needs to re-process raw streams.
    CTL/ATL/TSB here are derived from actual ride TSS (not plan estimates).
    """

    __tablename__ = "ride_metrics"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    strava_activity_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    activity_date: Mapped[str] = mapped_column(String(20), nullable=False)
    sport_type: Mapped[str] = mapped_column(String(50), default="cycling", nullable=False)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    avg_power_w: Mapped[int | None] = mapped_column(Integer, nullable=True)
    normalized_power_w: Mapped[int | None] = mapped_column(Integer, nullable=True)
    intensity_factor: Mapped[float | None] = mapped_column(nullable=True)
    tss: Mapped[float | None] = mapped_column(nullable=True)
    ftp_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ctl_after: Mapped[float | None] = mapped_column(nullable=True)
    atl_after: Mapped[float | None] = mapped_column(nullable=True)
    tsb_after: Mapped[float | None] = mapped_column(nullable=True)
    ride_purpose: Mapped[str | None] = mapped_column(String(50), nullable=True)
    classification_confidence: Mapped[str | None] = mapped_column(String(10), nullable=True)
    classification_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    coach_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    coach_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    user: Mapped["User"] = relationship(back_populates="ride_metrics")
