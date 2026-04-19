"""SQLAlchemy ORM models."""

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


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
    threshold_heart_rate: Mapped[int | None] = mapped_column(Integer)
    current_ftp: Mapped[int | None] = mapped_column(Integer)
    fitness_level: Mapped[str | None] = mapped_column(String(50))
    ai_provider: Mapped[str] = mapped_column(String(20), default="openai")

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

    user: Mapped["User"] = relationship(back_populates="workout_logs")


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
    access_token: Mapped[str] = mapped_column(String(255), nullable=False)
    refresh_token: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    athlete_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    athlete_name: Mapped[str] = mapped_column(String(255), default="")

    user: Mapped["User"] = relationship(back_populates="strava_token")


class RiderAssessment(Base):
    __tablename__ = "rider_assessments"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), primary_key=True
    )
    estimated_ftp: Mapped[int | None] = mapped_column(Integer)
    estimated_threshold_hr: Mapped[int | None] = mapped_column(Integer)
    rider_type: Mapped[str] = mapped_column(String(50), nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    hr_zones: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    ride_insights: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_ride_feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    threshold_hr: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ctl: Mapped[float | None] = mapped_column(nullable=True)
    atl: Mapped[float | None] = mapped_column(nullable=True)
    tsb: Mapped[float | None] = mapped_column(nullable=True)
    source: Mapped[str] = mapped_column(String(50), default="strava_analysis")

    user: Mapped["User"] = relationship(back_populates="athlete_metric_snapshots")
