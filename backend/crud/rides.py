"""Ride metrics: storing a ride once, finding it again, what the athlete adds."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import String, case, cast, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

import models
from domain import plan_compliance, ride_purpose_question
from domain.activity_identity import are_near_duplicate_activities
from domain.ride_summary import build_rule_based_summary


# Activity sources whose external id is the session's whole identity, so
# that two different ids from one of them are two different sessions. Kept as
# a literal rather than imported to avoid crud depending on services; a test
# pins it against ``logged_sessions.LOGGED_SOURCE``.
EXACT_IDENTITY_SOURCES: frozenset[str] = frozenset({"logged"})


def _ride_metrics_are_near_duplicates(
    ride: models.RideMetric,
    other: models.RideMetric,
) -> bool:
    # Two rows of one exact-identity source are the same session only if they
    # carry the same id — and then the SQL ranking has already collapsed them.
    # Different ids are different sessions, however alike they look (#47).
    if (
        ride.activity_source in EXACT_IDENTITY_SOURCES
        and ride.activity_source == other.activity_source
    ):
        return False
    return are_near_duplicate_activities(
        activity_date=ride.activity_date,
        sport_type=ride.sport_type,
        activity_name=ride.activity_name,
        activity_start_datetime=ride.activity_start_datetime,
        duration_seconds=ride.duration_seconds,
        other_activity_date=other.activity_date,
        other_sport_type=other.sport_type,
        other_activity_name=other.activity_name,
        other_activity_start_datetime=other.activity_start_datetime,
        other_duration_seconds=other.duration_seconds,
    )


def _preferred_visible_ride_metric(
    ride: models.RideMetric,
    other: models.RideMetric,
) -> models.RideMetric:
    ride_duration = ride.duration_seconds or 0
    other_duration = other.duration_seconds or 0
    if abs(ride_duration - other_duration) > 15 * 60:
        return ride if ride_duration > other_duration else other
    return ride


def _should_preserve_existing_near_duplicate(
    existing: models.RideMetric,
    incoming_duration_seconds: int | float | None,
) -> bool:
    existing_duration = existing.duration_seconds or 0
    incoming_duration = incoming_duration_seconds or 0
    return existing_duration - incoming_duration > 15 * 60


# ---------------------------------------------------------------------------
# Ride metrics
# ---------------------------------------------------------------------------


async def get_all_ride_metrics_ordered(
    db: AsyncSession,
    user_id: str,
) -> list[models.RideMetric]:
    """Return all RideMetric rows for a user sorted by activity_date ascending.

    Used when rebuilding the full CTL/ATL/TSB chain after an FTP change or an
    upload. Two sessions on one day are ordered by start time, then by when
    they were stored, then by id: the reverse of how ``get_latest_ride_metric``
    picks the newest, so the replay's last row is the one the summary reads. By
    date alone their order was whatever the database returned, and the replay
    could hand them different fitness from one run to the next.
    """
    result = await db.scalars(
        select(models.RideMetric)
        .where(models.RideMetric.user_id == user_id)
        .order_by(
            models.RideMetric.activity_date.asc(),
            models.RideMetric.activity_start_datetime.asc().nulls_first(),
            models.RideMetric.created_at.asc(),
            models.RideMetric.id.asc(),
        )
    )
    return list(result)


async def delete_all_ride_metrics(db: AsyncSession, user_id: str) -> None:
    """Delete all RideMetric rows for a user and flush."""
    await db.execute(
        delete(models.RideMetric).where(models.RideMetric.user_id == user_id)
    )
    await db.flush()


# What a re-import must not overwrite once the athlete has said what the session
# was. The classification is theirs, and ``summary`` restates it in prose — a
# recomputed "Unknown ride · 58m" beside ``ride_purpose = interval_threshold``
# is a contradiction the coach reads out loud (#580).
_ATHLETE_OWNED_RIDE_METRIC_FIELDS = (
    "ride_purpose",
    "classification_confidence",
    "classification_reason",
    "summary",
)


def _assign_ride_metric_values(row: models.RideMetric, values: dict) -> None:
    """Write *values* onto *row*, leaving the athlete's own answer intact.

    Import re-runs on every sync and rebuilds the classification from scratch.
    Without this gate the athlete answers "that was 4×8 at threshold", the next
    sync silently puts it back to ``unknown``/``low``, and the question they
    already answered reappears — the stale-snapshot clobber shape again, so the
    fix is again a persistent marker rather than a request-time snapshot.
    """
    protected = (
        row.classification_confidence == ride_purpose_question.ATHLETE_STATED_CONFIDENCE
    )
    for attr, val in values.items():
        if protected and attr in _ATHLETE_OWNED_RIDE_METRIC_FIELDS:
            continue
        setattr(row, attr, val)


def _guarded_conflict_value(column: str, value: object):
    """The same protection as :func:`_assign_ride_metric_values`, in SQL.

    This path only runs when a row appeared between the SELECT above and the
    INSERT, so it is the narrow race rather than the normal re-import. Stating
    the rule here too means the invariant holds always instead of usually.
    """
    if column not in _ATHLETE_OWNED_RIDE_METRIC_FIELDS:
        return value
    existing = getattr(models.RideMetric, column)
    return case(
        (
            models.RideMetric.classification_confidence
            == ride_purpose_question.ATHLETE_STATED_CONFIDENCE,
            existing,
        ),
        else_=value,
    )


async def upsert_ride_metric(
    db: AsyncSession,
    user_id: str,
    *,
    strava_activity_id: int,
    activity_source: str = "strava",
    external_activity_id: str | None = None,
    source_metadata: dict | None = None,
    activity_date: str,
    sport_type: str = "cycling",
    activity_name: str | None = None,
    activity_start_datetime: str | None = None,
    duration_seconds: int | None = None,
    distance_km: float | None = None,
    elevation_gain_m: float | None = None,
    start_lat: float | None = None,
    start_lng: float | None = None,
    weather_temperature_c: float | None = None,
    weather_apparent_temperature_c: float | None = None,
    weather_condition: str | None = None,
    weather_code: int | None = None,
    weather_wind_speed_kph: float | None = None,
    weather_precipitation_mm: float | None = None,
    weather_source: str | None = None,
    avg_power_w: int | None = None,
    avg_heart_rate_bpm: int | None = None,
    normalized_power_w: int | None = None,
    intensity_factor: float | None = None,
    tss: float | None = None,
    tss_source: str | None = None,
    ftp_used: int | None = None,
    ctl_after: float | None = None,
    atl_after: float | None = None,
    tsb_after: float | None = None,
    ctl_by_sport: dict[str, float] | None = None,
    ride_purpose: str | None = None,
    classification_confidence: str | None = None,
    classification_reason: str | None = None,
    perf_signals: dict | None = None,
    summary: str | None = None,
) -> models.RideMetric:
    """Insert or update a RideMetric row by stable activity identity."""
    normalized_source = activity_source or "strava"
    normalized_external_id = (
        str(external_activity_id)
        if external_activity_id is not None
        else str(strava_activity_id)
    )
    use_source_key = normalized_source != "strava" or external_activity_id is not None
    values = dict(
        user_id=user_id,
        strava_activity_id=strava_activity_id,
        activity_source=normalized_source,
        external_activity_id=normalized_external_id,
        source_metadata=source_metadata,
        activity_date=activity_date,
        sport_type=sport_type,
        activity_name=activity_name,
        activity_start_datetime=activity_start_datetime,
        duration_seconds=duration_seconds,
        start_lat=start_lat,
        start_lng=start_lng,
        weather_temperature_c=weather_temperature_c,
        weather_apparent_temperature_c=weather_apparent_temperature_c,
        weather_condition=weather_condition,
        weather_code=weather_code,
        weather_wind_speed_kph=weather_wind_speed_kph,
        weather_precipitation_mm=weather_precipitation_mm,
        weather_source=weather_source,
        avg_power_w=avg_power_w,
        avg_heart_rate_bpm=avg_heart_rate_bpm,
        normalized_power_w=normalized_power_w,
        intensity_factor=intensity_factor,
        tss=tss,
        tss_source=tss_source,
        ftp_used=ftp_used,
        ctl_after=ctl_after,
        atl_after=atl_after,
        tsb_after=tsb_after,
        ctl_by_sport=ctl_by_sport,
        ride_purpose=ride_purpose,
        classification_confidence=classification_confidence,
        classification_reason=classification_reason,
        perf_signals=perf_signals,
        summary=summary,
    )
    # Only what was reported. Every import path re-runs on every sync, and not
    # all of them carry these (a FIT file or a hand-logged session may not), so a
    # missing value must never erase one an earlier sync stored (ai-trainer-ops#65).
    if distance_km is not None:
        values["distance_km"] = distance_km
    if elevation_gain_m is not None:
        values["elevation_gain_m"] = elevation_gain_m

    exact_existing = (
        await get_ride_metric_by_source(
            db,
            user_id,
            normalized_source,
            normalized_external_id,
        )
        if use_source_key
        else await get_ride_metric_by_strava_id(db, user_id, strava_activity_id)
    )
    if exact_existing is not None:
        _assign_ride_metric_values(exact_existing, values)
        await db.flush()
        return exact_existing

    near_duplicate = await get_near_duplicate_ride_metric(
        db,
        user_id,
        activity_date=activity_date,
        sport_type=sport_type,
        activity_name=activity_name,
        activity_start_datetime=activity_start_datetime,
        duration_seconds=duration_seconds,
        exclude_strava_activity_id=strava_activity_id,
        exclude_activity_source=normalized_source,
        exclude_external_activity_id=normalized_external_id,
    )
    if near_duplicate is not None:
        if _should_preserve_existing_near_duplicate(
            near_duplicate,
            duration_seconds,
        ):
            return near_duplicate
        _assign_ride_metric_values(near_duplicate, values)
        await db.flush()
        return near_duplicate

    conn = await db.connection()
    if conn.dialect.name == "postgresql":
        conflict_keys = (
            ["user_id", "activity_source", "external_activity_id"]
            if use_source_key
            else ["user_id", "strava_activity_id"]
        )
        stmt = (
            pg_insert(models.RideMetric)
            .values(**values, id=str(__import__("uuid").uuid4()))
            .on_conflict_do_update(
                index_elements=conflict_keys,
                set_={
                    k: _guarded_conflict_value(k, v)
                    for k, v in values.items()
                    if k not in conflict_keys
                },
            )
            .returning(models.RideMetric)
        )
        result = await db.execute(stmt)
        return result.scalar_one()

    # SQLite (tests) — SELECT + update/insert; flush immediately to avoid autoflush issues
    if use_source_key:
        existing = await db.scalar(
            select(models.RideMetric)
            .where(
                models.RideMetric.user_id == user_id,
                models.RideMetric.activity_source == normalized_source,
                models.RideMetric.external_activity_id == normalized_external_id,
            )
            .order_by(
                models.RideMetric.created_at.desc(),
                models.RideMetric.activity_date.desc(),
                func.coalesce(models.RideMetric.activity_start_datetime, "").desc(),
                models.RideMetric.id.desc(),
            )
            .limit(1)
        )
    else:
        existing = await db.scalar(
            select(models.RideMetric).where(
                models.RideMetric.user_id == user_id,
                models.RideMetric.strava_activity_id == strava_activity_id,
            )
        )
    if existing is not None:
        _assign_ride_metric_values(existing, values)
        await db.flush()
        return existing

    row = models.RideMetric(id=str(__import__("uuid").uuid4()), **values)
    db.add(row)
    await db.flush()
    return row


async def get_ride_metrics_history(
    db: AsyncSession,
    user_id: str,
    limit: int = 60,
) -> list[models.RideMetric]:
    """Return the most recent *limit* visible RideMetric rows, newest first.

    Stale duplicate rows are collapsed by source/external activity identity so
    dashboards never show the same imported activity twice.
    """
    source_key = func.coalesce(models.RideMetric.activity_source, "strava")
    external_key = func.coalesce(
        models.RideMetric.external_activity_id,
        cast(models.RideMetric.strava_activity_id, String),
    )
    start_key = func.coalesce(models.RideMetric.activity_start_datetime, "")
    ranked = (
        select(
            models.RideMetric.id.label("ride_metric_id"),
            func.row_number()
            .over(
                partition_by=(
                    models.RideMetric.user_id,
                    source_key,
                    external_key,
                ),
                order_by=(
                    models.RideMetric.activity_date.desc(),
                    start_key.desc(),
                    models.RideMetric.id.desc(),
                ),
            )
            .label("dedupe_rank"),
        )
        .where(models.RideMetric.user_id == user_id)
        .subquery()
    )
    result = await db.scalars(
        select(models.RideMetric)
        .join(ranked, models.RideMetric.id == ranked.c.ride_metric_id)
        .where(ranked.c.dedupe_rank == 1)
        .order_by(
            models.RideMetric.activity_date.desc(),
            start_key.desc(),
            models.RideMetric.id.desc(),
        )
        .limit(max(limit * 3, limit))
    )
    rides = list(result)
    deduped: list[models.RideMetric] = []
    for ride in rides:
        duplicate_index = next(
            (
                index
                for index, existing in enumerate(deduped)
                if _ride_metrics_are_near_duplicates(ride, existing)
            ),
            None,
        )
        if duplicate_index is not None:
            deduped[duplicate_index] = _preferred_visible_ride_metric(
                ride,
                deduped[duplicate_index],
            )
            continue
        deduped.append(ride)
        if len(deduped) >= limit:
            break
    return deduped


async def get_ride_metrics_missing_weather(
    db: AsyncSession,
    user_id: str,
    limit: int = 90,
) -> list[models.RideMetric]:
    """Return recent RideMetric rows that do not yet have weather attached."""
    result = await db.scalars(
        select(models.RideMetric)
        .where(
            models.RideMetric.user_id == user_id,
            models.RideMetric.weather_temperature_c.is_(None),
        )
        .order_by(models.RideMetric.activity_date.desc())
        .limit(limit)
    )
    return list(result)


async def get_latest_ride_metric_with_location(
    db: AsyncSession,
    user_id: str,
) -> models.RideMetric | None:
    """Return the latest ride metric with usable start coordinates."""
    return await db.scalar(
        select(models.RideMetric)
        .where(
            models.RideMetric.user_id == user_id,
            models.RideMetric.start_lat.is_not(None),
            models.RideMetric.start_lng.is_not(None),
        )
        .order_by(models.RideMetric.activity_date.desc())
        .limit(1)
    )


async def get_recent_ride_start_locations(
    db: AsyncSession,
    user_id: str,
    limit: int = 120,
) -> list[tuple[float, float]]:
    """Return recent ride start coordinates, newest first (#495).

    The learning substrate for the inferred home location: clustering these is
    robust to travel and one-off rides in a way the single latest GPS ride never
    was (:func:`services.home_location.infer_home_location`).
    """
    rows = await db.execute(
        select(models.RideMetric.start_lat, models.RideMetric.start_lng)
        .where(
            models.RideMetric.user_id == user_id,
            models.RideMetric.start_lat.is_not(None),
            models.RideMetric.start_lng.is_not(None),
        )
        .order_by(models.RideMetric.activity_date.desc())
        .limit(limit)
    )
    return [(float(lat), float(lng)) for lat, lng in rows.all()]


async def get_rides_with_weather(
    db: AsyncSession,
    user_id: str,
    limit: int = 200,
) -> list[models.RideMetric]:
    """Return recent rides that carry stored weather, newest first (#495).

    Feeds the weather-preference belief update: the per-ride conditions were
    already persisted at import, so learning tolerances needs no new storage.
    """
    result = await db.scalars(
        select(models.RideMetric)
        .where(
            models.RideMetric.user_id == user_id,
            models.RideMetric.weather_temperature_c.is_not(None),
        )
        .order_by(models.RideMetric.activity_date.desc())
        .limit(limit)
    )
    return list(result)


async def get_latest_ride_metric(
    db: AsyncSession,
    user_id: str,
) -> models.RideMetric | None:
    """Return the single most recent RideMetric for a user.

    Used to seed CTL/ATL for incremental chain continuation, and read as "the
    most recent activity" by the training summary and its feedback.
    """
    return await db.scalar(
        select(models.RideMetric)
        .where(models.RideMetric.user_id == user_id)
        .order_by(
            models.RideMetric.activity_date.desc(),
            # A ride and a gym session on one day is common. Ordered by date
            # alone, the "latest" of them was whichever row came back first, and
            # two reads could disagree.
            models.RideMetric.activity_start_datetime.desc().nulls_last(),
            models.RideMetric.created_at.desc(),
        )
        .limit(1)
    )


async def update_ride_metric_notes(
    db: AsyncSession,
    user_id: str,
    strava_activity_id: int,
    *,
    coach_note: str | None = None,
    user_note: str | None = None,
    label_override: str | None = None,
) -> models.RideMetric | None:
    """Partially update coach_note, user_note, and/or label_override on a RideMetric row.

    Only overwrites fields whose values are explicitly provided (not None).
    Returns the updated row, or None if not found.
    """
    row = await db.scalar(
        select(models.RideMetric).where(
            models.RideMetric.user_id == user_id,
            models.RideMetric.strava_activity_id == strava_activity_id,
        )
    )
    if row is None:
        return None
    if coach_note is not None:
        row.coach_note = coach_note
    if user_note is not None:
        row.user_note = user_note
    if label_override is not None:
        row.label_override = label_override
    return row


async def get_unclassified_intervals_ride_metrics(
    db: AsyncSession,
    user_id: str,
    *,
    since_date: str,
    limit: int = 25,
) -> list[models.RideMetric]:
    """Return recent intervals.icu rides still classified ``unknown`` (or NULL).

    Backs the reclassification backfill: bounded by ``since_date`` and ``limit``
    so it only ever re-fetches a small, recent set of laps from the provider
    (#482).  Rows the provider has already answered 404 for are excluded — their
    id is permanently dead, and without this they would occupy the bounded
    candidate slots on every tick forever (#517).  Newest first.
    """
    result = await db.scalars(
        select(models.RideMetric)
        .where(
            models.RideMetric.user_id == user_id,
            models.RideMetric.activity_source == "intervals",
            or_(
                models.RideMetric.ride_purpose == "unknown",
                models.RideMetric.ride_purpose.is_(None),
            ),
            models.RideMetric.activity_date >= since_date,
            models.RideMetric.provider_unfetchable_at.is_(None),
        )
        .order_by(models.RideMetric.activity_date.desc())
        .limit(limit)
    )
    return list(result)


async def update_ride_metric_classification(
    row: models.RideMetric,
    *,
    ride_purpose: str,
    classification_confidence: str | None,
    classification_reason: str | None,
    summary: str | None = None,
) -> None:
    """Overwrite only the auto-classification fields on a RideMetric row.

    Leaves power/TSS/CTL/ATL and every athlete-edited field (label_override,
    coach_note, feel_legs, plan match, …) untouched, so a reclassification never
    clobbers user edits.

    The classification itself is now one of those edits when the athlete has
    stated what the session was (#580), so this returns early rather than
    replacing their account of it with an inference.
    """
    if row.classification_confidence == ride_purpose_question.ATHLETE_STATED_CONFIDENCE:
        return
    row.ride_purpose = ride_purpose
    row.classification_confidence = classification_confidence
    row.classification_reason = classification_reason
    if summary is not None:
        row.summary = summary


async def mark_ride_metric_unfetchable(row: models.RideMetric) -> None:
    """Record that the provider answered 404 for this row's activity id (#517).

    Touches nothing else: the ride's own metrics stay exactly as imported, only
    the "do not ask the provider about this id again" marker is set.
    """
    row.provider_unfetchable_at = datetime.now(timezone.utc)


async def set_ride_feel_legs(
    db: AsyncSession,
    user_id: str,
    strava_activity_id: int,
    legs: str | None,
    *,
    external_activity_id: str | None = None,
) -> models.RideMetric | None:
    """Set (or clear, when ``legs`` is None) the athlete's leg-freshness rating.

    Unlike :func:`update_ride_metric_notes`, this always assigns the value so a
    dashboard tap can clear a prior rating.  It never touches ``user_note`` or
    any other field, so it cannot clobber notes captured conversationally.
    Returns the updated row, or None if not found.

    ``external_activity_id`` is the precision-safe string identity carried for
    non-Strava rides (e.g. intervals.icu).  Their synthesized 63-bit
    ``strava_activity_id`` is float64-corrupted through the browser, so an exact
    int lookup misses (#441); prefer the string id when the frontend supplies it.
    """
    row = await get_ride_metric_by_identity(
        db, user_id, strava_activity_id, external_activity_id
    )
    if row is None:
        return None
    row.feel_legs = legs
    return row


async def answer_ride_purpose_question(
    db: AsyncSession,
    user_id: str,
    strava_activity_id: int,
    purpose: str | None,
    *,
    external_activity_id: str | None = None,
) -> models.RideMetric | None:
    """Record what the athlete said this session was, or that they skipped (#580).

    ``purpose`` is one of :data:`ride_purpose_question.ATHLETE_PURPOSE_CHOICES`;
    ``None`` records a skip, which stops the question without asserting anything
    about the session. Both are terminal — a question the athlete has waved away
    must not come back, which is the same restraint ``ATHLETE_INQUIRY_MAX_ASKS``
    exists to enforce for the coach's other questions.

    An answer writes the classification itself, not a note beside it, so every
    prompt and every badge that reads ``ride_purpose`` sees the athlete's answer
    without knowing this feature exists. The confidence records who said so and
    is what keeps the next sync from overwriting it
    (:func:`_assign_ride_metric_values`).

    ``external_activity_id`` is the precision-safe string identity carried for
    non-Strava rides; their synthesized 63-bit ``strava_activity_id`` is
    float64-corrupted through the browser (#441).
    """
    row = await get_ride_metric_by_identity(
        db, user_id, strava_activity_id, external_activity_id
    )
    if row is None:
        return None

    if purpose is None:
        row.purpose_question_status = ride_purpose_question.QUESTION_SKIPPED
        return row

    row.ride_purpose = purpose
    row.classification_confidence = ride_purpose_question.ATHLETE_STATED_CONFIDENCE
    row.classification_reason = ride_purpose_question.athlete_answer_reason(purpose)
    row.purpose_question_status = ride_purpose_question.QUESTION_ANSWERED
    # The stored one-line summary restates the classification, so leaving it
    # would put "Unknown ride" next to the athlete's own answer.
    row.summary = build_rule_based_summary(
        purpose,
        float(row.duration_seconds or 0),
        float(row.normalized_power_w) if row.normalized_power_w else None,
        float(row.tss) if row.tss is not None else None,
        [],
        tss_source=row.tss_source,
    )
    return row


async def get_ride_metric_by_identity(
    db: AsyncSession,
    user_id: str,
    strava_activity_id: int,
    external_activity_id: str | None = None,
) -> models.RideMetric | None:
    """Find the ride a client is pointing at. The one gate for that question.

    Non-Strava activities carry a synthesized 63-bit ``strava_activity_id``.
    JavaScript numbers are float64, so anything above 2^53 is rounded on the way
    through the browser — ``7846148097020609552`` comes back as ``...609536`` and
    matches no row. The provider's own string id is the identity that survives.

    The numeric id stays the fallback: Strava's own ids are well inside float64,
    and a client that sends no string still has to work.

    This exists because the same three lines were written out at every write path
    that names a ride, and the fourth one to be added forgot them — every resolve
    of an ambiguous intervals ride 404'd for two weeks (#441, #574, PR#588). A
    rule that has to be remembered at each new endpoint is a rule that will be
    forgotten at one of them; this is the version that cannot be.
    """
    if external_activity_id:
        row = await get_ride_metric_by_external_id(db, user_id, external_activity_id)
        if row is not None:
            return row
    return await get_ride_metric_by_strava_id(db, user_id, strava_activity_id)


async def get_ride_metric_by_external_id(
    db: AsyncSession,
    user_id: str,
    external_activity_id: str,
) -> models.RideMetric | None:
    """Return the RideMetric for a provider's external activity id, or None.

    External ids are precision-safe strings (unlike the float64-corrupted
    synthesized ``strava_activity_id`` for non-Strava rides, #441).
    """
    return await get_ride_metric_by_source(db, user_id, None, external_activity_id)


async def get_ride_metric_by_strava_id(
    db: AsyncSession,
    user_id: str,
    strava_activity_id: int,
) -> models.RideMetric | None:
    """Return the RideMetric for a given Strava activity ID, or None if not found."""
    return await db.scalar(
        select(models.RideMetric).where(
            models.RideMetric.user_id == user_id,
            models.RideMetric.strava_activity_id == strava_activity_id,
        )
    )


async def get_ride_metric_by_source(
    db: AsyncSession,
    user_id: str,
    activity_source: str | None,
    external_activity_id: str,
) -> models.RideMetric | None:
    """The RideMetric with this external id from ``activity_source``, or from
    any source when it is ``None``. The one query for both (ai-trainer-ops#250)."""
    query = select(models.RideMetric).where(
        models.RideMetric.user_id == user_id,
        models.RideMetric.external_activity_id == external_activity_id,
    )
    if activity_source is not None:
        query = query.where(models.RideMetric.activity_source == activity_source)
    return await db.scalar(query)


async def get_near_duplicate_ride_metric(
    db: AsyncSession,
    user_id: str,
    *,
    activity_date: str,
    sport_type: str | None,
    activity_name: str | None,
    activity_start_datetime: str | None,
    duration_seconds: int | float | None,
    exclude_strava_activity_id: int | None = None,
    exclude_activity_source: str | None = None,
    exclude_external_activity_id: str | None = None,
) -> models.RideMetric | None:
    """Return a likely duplicate imported activity for the same user/date."""
    result = await db.scalars(
        select(models.RideMetric)
        .where(
            models.RideMetric.user_id == user_id,
            models.RideMetric.activity_date == activity_date,
        )
        .order_by(
            models.RideMetric.created_at.desc(),
            func.coalesce(models.RideMetric.activity_start_datetime, "").desc(),
            models.RideMetric.id.desc(),
        )
    )
    for ride in result:
        if (
            exclude_strava_activity_id is not None
            and ride.strava_activity_id == exclude_strava_activity_id
        ):
            continue
        if (
            exclude_activity_source is not None
            and exclude_external_activity_id is not None
            and ride.activity_source == exclude_activity_source
            and ride.external_activity_id == exclude_external_activity_id
        ):
            continue
        # A source whose ids *are* the session's identity never has near
        # duplicates within itself. Two hand-logged sessions carry no start time
        # and one shared name, so the fuzzy match below took a second run on the
        # same day for the first and overwrote it — one session of fatigue where
        # the athlete did two (ai-trainer-ops#47). Matching a logged session
        # against a *recording* stays, which is how a recording retires it.
        if (
            exclude_activity_source in EXACT_IDENTITY_SOURCES
            and ride.activity_source == exclude_activity_source
        ):
            continue
        if are_near_duplicate_activities(
            activity_date=activity_date,
            sport_type=sport_type,
            activity_name=activity_name,
            activity_start_datetime=activity_start_datetime,
            duration_seconds=duration_seconds,
            other_activity_date=ride.activity_date,
            other_sport_type=ride.sport_type,
            other_activity_name=ride.activity_name,
            other_activity_start_datetime=ride.activity_start_datetime,
            other_duration_seconds=ride.duration_seconds,
        ):
            return ride
    return None


async def get_ride_metric_by_date(
    db: AsyncSession,
    user_id: str,
    activity_date: str,
) -> models.RideMetric | None:
    """Return the RideMetric whose activity_date matches the given ISO date string.

    Used to resolve chat-inferred user feedback to a specific ride.
    """
    return await db.scalar(
        select(models.RideMetric).where(
            models.RideMetric.user_id == user_id,
            models.RideMetric.activity_date == activity_date,
        )
    )


async def get_ride_metrics_by_date(
    db: AsyncSession,
    user_id: str,
    activity_date: str,
) -> list[models.RideMetric]:
    """Return all RideMetrics for a user on a given ISO date string.

    Used by ride-matching logic to handle multiple rides on the same day.
    """
    result = await db.scalars(
        select(models.RideMetric).where(
            models.RideMetric.user_id == user_id,
            models.RideMetric.activity_date == activity_date,
        )
    )
    return list(result)


async def get_recorded_activity_dates(
    db: AsyncSession, user_id: str, dates: list[str]
) -> set[str]:
    """Return the subset of ``dates`` on which the user has a recorded activity.

    Used by the plan pipeline to protect past days the athlete actually trained
    from being dropped by an automated full-plan regenerate, independent of the
    manual ``completed`` flag (which is only set when the athlete ticks a workout
    by hand). Every synced activity — ride, strength, yoga — is a ``RideMetric``,
    so its presence marks the date as historical fact.
    """
    if not dates:
        return set()
    result = await db.scalars(
        select(models.RideMetric.activity_date)
        .where(
            models.RideMetric.user_id == user_id,
            models.RideMetric.activity_date.in_(dates),
        )
        .distinct()
    )
    return set(result)


# ---------------------------------------------------------------------------
# Ride review helpers
# ---------------------------------------------------------------------------


async def get_unreviewed_ride_metrics(
    db: AsyncSession, user_id: str
) -> list[models.RideMetric]:
    """Return all RideMetric rows for a user where coach_reviewed_at is NULL, ordered oldest-first."""
    result = await db.execute(
        select(models.RideMetric)
        .where(
            models.RideMetric.user_id == user_id,
            models.RideMetric.coach_reviewed_at.is_(None),
        )
        .order_by(
            models.RideMetric.activity_date,
            models.RideMetric.activity_start_datetime,
            models.RideMetric.strava_activity_id,
        )
    )
    return list(result.scalars().all())


async def mark_rides_as_reviewed(
    db: AsyncSession, user_id: str, strava_activity_ids: list[int]
) -> int:
    """Set coach_reviewed_at to now for the given activity IDs. Returns count updated."""
    if not strava_activity_ids:
        return 0
    now = datetime.now(timezone.utc)
    result = await db.execute(
        update(models.RideMetric)
        .where(
            models.RideMetric.user_id == user_id,
            models.RideMetric.strava_activity_id.in_(strava_activity_ids),
        )
        .values(coach_reviewed_at=now)
    )
    await db.flush()
    return result.rowcount


def _activity_key_filters(activity_keys: list[int | str]) -> list:
    numeric_ids: list[int] = []
    external_ids: list[str] = []
    for key in activity_keys:
        key_text = str(key)
        external_ids.append(key_text)
        if key_text.isdigit():
            numeric_ids.append(int(key_text))
    filters = []
    if numeric_ids:
        filters.append(models.RideMetric.strava_activity_id.in_(numeric_ids))
    if external_ids:
        filters.append(models.RideMetric.external_activity_id.in_(external_ids))
    return filters


async def get_ride_metrics_by_activity_ids(
    db: AsyncSession, user_id: str, strava_activity_ids: list[int | str]
) -> list[models.RideMetric]:
    """Return RideMetric rows for the given activity keys, ordered oldest-first."""
    filters = _activity_key_filters(strava_activity_ids)
    if not filters:
        return []
    result = await db.execute(
        select(models.RideMetric)
        .where(
            models.RideMetric.user_id == user_id,
            or_(*filters),
        )
        .order_by(
            models.RideMetric.activity_date,
            models.RideMetric.activity_start_datetime,
            models.RideMetric.strava_activity_id,
        )
    )
    return list(result.scalars().all())


async def update_ride_match(
    db: AsyncSession,
    ride: models.RideMetric,
    *,
    status: str,
    matched_plan_date: str | None = None,
    matched_plan_slot: int | None = None,
    matched_plan_snapshot: dict | None = None,
    matched_at: datetime | None = None,
    label_override: str | None = None,
) -> models.RideMetric:
    """Update the plan-match fields on a RideMetric row and flush.

    ``matched_plan_slot`` names which session on ``matched_plan_date`` the ride
    belongs to (#496); ``None`` is the single-session day.

    The compliance badge is rescored here because this is the one funnel every
    match write passes through, so the stored badge can never drift from the
    plan day the ride is currently matched to (#551).
    """
    ride.plan_match_status = status
    ride.matched_plan_date = matched_plan_date
    ride.matched_plan_slot = matched_plan_slot
    ride.matched_plan_snapshot = matched_plan_snapshot
    ride.matched_at = matched_at
    ride.label_override = label_override
    ride.match_score, ride.match_label = plan_compliance.score_and_label(
        ride, matched_plan_snapshot
    )
    await db.flush()
    return ride
