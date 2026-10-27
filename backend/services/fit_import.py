"""Importing a .fit file: parse it, unpack a zip of them, and store each as a ride (ai-trainer-ops#251).

This was some 600 lines of routers/users.py, and routers/tools.py imported three
of its private helpers from there. The routes stay in the routers; what an
upload is, and what storing one does, lives here. The two errors an upload can
meet before parsing are this module's own (:class:`FitParserUnavailable`,
:class:`UploadTooLarge`): routers/dependencies.py turns them into 503 and 413.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import zipfile
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
import schemas
from config import settings
from services import (
    ai_service,
    assessment_pipeline,
    reported_effort,
    ride_ingest,
    rides_pipeline,
    starting_hypothesis_check,
)
from services.activity_imports import ImportedActivity, find_existing_import
from services.analysis import AVG_POWER_TO_FTP_RATIO
from services.token_accounting import track_llm_usage

logger = logging.getLogger(__name__)


@dataclass
class ParsedFitActivity:
    source_id: int
    source_metadata: dict[str, Any]
    sport_type: str
    duration_seconds: int
    duration_minutes: int
    ride_date: str
    completed_at: str
    activity_name: str
    avg_power: int | None
    avg_hr: int | None
    start_lat: float | None
    start_lng: float | None
    streams: dict[str, dict[str, list[Any]]]

    def to_imported_activity(self) -> ImportedActivity:
        return ImportedActivity(
            source="fit",
            external_activity_id=str(self.source_id),
            name=self.activity_name,
            start_datetime=self.completed_at,
            activity_date=self.ride_date,
            sport_type=self.sport_type,
            duration_seconds=self.duration_seconds,
            start_lat=self.start_lat,
            start_lng=self.start_lng,
            streams=self.streams,
            metadata=self.source_metadata,
            summary_avg_power_w=self.avg_power,
            legacy_activity_id=self.source_id,
        )


def _fit_field_map(message: Any) -> dict[str, Any]:
    return {field.name: field.value for field in message}


def _as_datetime(value: Any) -> datetime | None:
    return value if isinstance(value, datetime) else None


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _average_int(values: list[int]) -> int | None:
    return round(sum(values) / len(values)) if values else None


def _semicircles_to_degrees(value: Any) -> float | None:
    numeric = _as_float(value)
    if numeric is None:
        return None
    return numeric * (180.0 / 2**31)


def _json_safe_fit_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def upload_source_id(raw: bytes, metadata: dict[str, Any]) -> int:
    """The id an uploaded file is known by, so the same file is not booked twice."""
    metadata_basis = json.dumps(metadata, sort_keys=True, default=str)
    basis = metadata_basis if metadata else hashlib.sha256(raw).hexdigest()
    # 60 bits keeps the synthetic id positive and comfortably inside BIGINT.
    return int(hashlib.sha256(basis.encode()).hexdigest()[:15], 16)


def parse_fit_activity(raw: bytes, filename: str, FitFile: Any) -> ParsedFitActivity:
    try:
        fit = FitFile(io.BytesIO(raw))
        fit.parse()
    except Exception as exc:
        logger.warning("Failed to parse .fit file %s: %s", filename, exc)
        raise ValueError("Could not parse the uploaded .fit file") from exc

    sport_type = "cycling"
    total_elapsed_seconds = 0
    avg_power: int | None = None
    avg_hr: int | None = None
    start_time: datetime | None = None
    metadata: dict[str, Any] = {}

    for message_type in ("file_id", "activity"):
        for record in fit.get_messages(message_type):
            fields = _fit_field_map(record)
            for key in (
                "serial_number",
                "time_created",
                "manufacturer",
                "product",
                "timestamp",
                "local_timestamp",
            ):
                if key in fields and fields[key] is not None:
                    metadata[f"{message_type}_{key}"] = _json_safe_fit_value(
                        fields[key]
                    )

    for record in fit.get_messages("session"):
        fields = _fit_field_map(record)
        if fields.get("sport"):
            sport_type = str(fields["sport"]).lower()
        total_elapsed_seconds = (
            _as_int(fields.get("total_elapsed_time"))
            or _as_int(fields.get("total_timer_time"))
            or total_elapsed_seconds
        )
        avg_power = _as_int(fields.get("avg_power")) or avg_power
        avg_hr = _as_int(fields.get("avg_heart_rate")) or avg_hr
        start_time = _as_datetime(fields.get("start_time")) or start_time
        for key in (
            "start_time",
            "sport",
            "sub_sport",
            "total_elapsed_time",
            "total_timer_time",
            "enhanced_avg_speed",
        ):
            if key in fields and fields[key] is not None:
                metadata[f"session_{key}"] = _json_safe_fit_value(fields[key])

    timestamps: list[datetime] = []
    watts: list[int] = []
    watt_times: list[int] = []
    heart_rates: list[int] = []
    hr_times: list[int] = []
    cadences: list[int] = []
    cadence_times: list[int] = []
    altitudes: list[float] = []
    altitude_times: list[int] = []
    speeds: list[float] = []
    speed_times: list[int] = []
    latlng: list[list[float]] = []
    start_lat: float | None = None
    start_lng: float | None = None

    for record in fit.get_messages("record"):
        fields = _fit_field_map(record)
        timestamp = _as_datetime(fields.get("timestamp"))
        if timestamp is None:
            continue
        timestamps.append(timestamp)
        first_timestamp = timestamps[0]
        elapsed = max(0, int((timestamp - first_timestamp).total_seconds()))

        power = _as_int(fields.get("power"))
        if power is not None and power > 0:
            watts.append(power)
            watt_times.append(elapsed)

        heart_rate = _as_int(fields.get("heart_rate"))
        if heart_rate is not None and heart_rate > 0:
            heart_rates.append(heart_rate)
            hr_times.append(elapsed)

        cadence = _as_int(fields.get("cadence"))
        if cadence is not None and cadence > 0:
            cadences.append(cadence)
            cadence_times.append(elapsed)

        altitude = _as_float(fields.get("enhanced_altitude")) or _as_float(
            fields.get("altitude")
        )
        if altitude is not None:
            altitudes.append(round(altitude, 2))
            altitude_times.append(elapsed)

        speed = _as_float(fields.get("enhanced_speed")) or _as_float(
            fields.get("speed")
        )
        if speed is not None:
            speeds.append(round(speed, 3))
            speed_times.append(elapsed)

        lat = _semicircles_to_degrees(fields.get("position_lat"))
        lng = _semicircles_to_degrees(fields.get("position_long"))
        if lat is not None and lng is not None:
            point = [round(lat, 6), round(lng, 6)]
            latlng.append(point)
            if start_lat is None or start_lng is None:
                start_lat, start_lng = point

    if not total_elapsed_seconds and len(timestamps) >= 2:
        total_elapsed_seconds = int((timestamps[-1] - timestamps[0]).total_seconds())
    if start_time is None and timestamps:
        start_time = timestamps[0]
    if avg_power is None:
        avg_power = _average_int(watts)
    if avg_hr is None:
        avg_hr = _average_int(heart_rates)

    total_elapsed_seconds = max(1, total_elapsed_seconds)
    duration_minutes = max(1, round(total_elapsed_seconds / 60))
    completed_at = (
        start_time.isoformat() if start_time else datetime.now(timezone.utc).isoformat()
    )
    ride_date = completed_at[:10]

    streams: dict[str, dict[str, list[Any]]] = {}
    if watts and watt_times and len(watts) == len(watt_times):
        streams["watts"] = {"data": watts}
        streams["time"] = {"data": watt_times}
    elif speeds and speed_times and len(speeds) == len(speed_times):
        # A run has no power stream, so until #716 an uploaded run arrived with a
        # speed stream and no time base at all — and a speed without a clock is
        # not a pace. The speed samples' own timestamps become the primary time
        # stream, which is also what aligns ``velocity_smooth`` with it.
        streams["time"] = {"data": speed_times}
    if heart_rates:
        streams["heartrate"] = {"data": heart_rates}
        streams["heartrate_time"] = {"data": hr_times}
    if cadences:
        streams["cadence"] = {"data": cadences}
        streams["cadence_time"] = {"data": cadence_times}
    if altitudes:
        streams["altitude"] = {"data": altitudes}
        streams["altitude_time"] = {"data": altitude_times}
    if speeds:
        streams["velocity_smooth"] = {"data": speeds}
        streams["velocity_time"] = {"data": speed_times}
    if latlng:
        streams["latlng"] = {"data": latlng}

    if start_time is not None:
        metadata.setdefault("activity_start_time", start_time.isoformat())
    metadata.setdefault("sport_type", sport_type)
    metadata.setdefault("duration_seconds", total_elapsed_seconds)
    source_id = upload_source_id(raw, metadata)

    return ParsedFitActivity(
        source_id=source_id,
        source_metadata=metadata,
        sport_type=sport_type,
        duration_seconds=total_elapsed_seconds,
        duration_minutes=duration_minutes,
        ride_date=ride_date,
        completed_at=completed_at,
        activity_name=f"FIT {sport_type.title()} {ride_date}",
        avg_power=avg_power,
        avg_hr=avg_hr,
        start_lat=start_lat,
        start_lng=start_lng,
        streams=streams,
    )


async def analyse_fit_import(
    db: AsyncSession,
    current_user: models.User,
    parsed: ParsedFitActivity,
    reply_language: str | None = None,
) -> dict | None:
    provider = current_user.ai_provider or "gemini"
    async with track_llm_usage(db, current_user, source="api:analyse-fit-import"):
        try:
            ai_result = await ai_service.analyse_fit_activity(
                sport_type=parsed.sport_type,
                duration_minutes=parsed.duration_minutes,
                avg_power=parsed.avg_power,
                avg_hr=parsed.avg_hr,
                max_heart_rate=current_user.max_heart_rate,
                provider=provider,
                reply_language=reply_language,
            )
        except Exception:
            logger.warning(
                "AI analysis failed for .fit upload; skipping feedback", exc_info=True
            )
            return None

    if ai_result:
        for field in ("rideInsights", "lastRideFeedback", "loginSummary", "notes"):
            value = ai_result.get(field)
            if value is not None and not isinstance(value, str):
                ai_result[field] = json.dumps(value)

        # These fields feed the login summary but no fresh summary was generated
        # here; record() marks it stale so it regenerates on the next load.
        await assessment_pipeline.record(
            db,
            current_user,
            estimated_ftp=ai_result.get("estimatedFTP"),
            rider_type=ai_result.get("riderType"),
            notes=ai_result.get("notes"),
            hr_zones=ai_result.get("hrZones"),
            ride_insights=ai_result.get("rideInsights"),
            last_ride_feedback=ai_result.get("lastRideFeedback"),
        )

    return ai_result


async def replay_after_fit_import(db: AsyncSession, user: models.User) -> None:
    """Put fitness and fatigue back in date order after an upload (ai-trainer-ops#156).

    Each uploaded file is chained on its own, seeded from the newest stored
    row as if it came the day after it. That holds for today's ride. It does
    not hold for an export of past months: those files arrive in any order and
    are older than what is stored, so every one of them was given the load
    picture of a different day, and the newest row never counted them. The
    loads themselves are right; replaying the chain over them in date order
    is what the FTP recalculation does too.
    """
    await ride_ingest.replay_load_chain(db, user.id)
    # The summary reads the newest row's fitness, which just moved, and the
    # status badge counts the week from the rows (ai-trainer-ops#245).
    await rides_pipeline.notify_changed(db, user)
    # An upload is how a new athlete without a connected service brings rides
    # at all, so the starting assumption is tested here too (ai-trainer-ops#159).
    # Best-effort, as in the learning step: the upload itself succeeded.
    try:
        async with db.begin_nested():
            await starting_hypothesis_check.check_starting_hypotheses(
                db, user, timezone_name=user.timezone_name
            )
    except Exception:
        logger.warning(
            "Starting hypothesis check failed for %s", user.id, exc_info=True
        )


# What an upload says per file, written out per format so each sentence is one
# entry in the app's German (frontend/src/i18n/de.ts, ai-trainer-ops#311).
_TRACK_KINDS = {"gpx": "GPX", "tcx": "TCX"}
_IMPORTED = {
    "FIT": "Imported FIT activity",
    "GPX": "Imported GPX activity",
    "TCX": "Imported TCX activity",
}
_ALREADY_IMPORTED = {
    "FIT": "FIT activity was already imported",
    "GPX": "GPX activity was already imported",
    "TCX": "TCX activity was already imported",
}
_DUPLICATE_IN_BATCH = {
    "FIT": "Duplicate FIT activity in this upload batch",
    "GPX": "Duplicate GPX activity in this upload batch",
    "TCX": "Duplicate TCX activity in this upload batch",
}


async def store_fit_import(
    db: AsyncSession,
    current_user: models.User,
    filename: str,
    parsed: ParsedFitActivity,
    seen_source_ids: set[int],
    *,
    analyse: bool = True,
    reply_language: str | None = None,
) -> schemas.FitUploadFileResult:
    # FIT, GPX or TCX: the format the file came in (ai-trainer-ops#311). Only the
    # track parser sets ``file_format``; a .fit's metadata is the file's own, and
    # whatever it says, the file is a .fit (review of #1186).
    kind = _TRACK_KINDS.get(parsed.source_metadata.get("file_format"), "FIT")
    if parsed.source_id in seen_source_ids:
        return schemas.FitUploadFileResult(
            filename=filename,
            status="skipped",
            message=_DUPLICATE_IN_BATCH[kind],
            activity_date=parsed.ride_date,
            sport_type=parsed.sport_type,
            duration_minutes=parsed.duration_minutes,
            average_power=parsed.avg_power,
            average_heart_rate=parsed.avg_hr,
        )

    seen_source_ids.add(parsed.source_id)
    imported_activity = parsed.to_imported_activity()
    existing_metric = await find_existing_import(db, current_user.id, imported_activity)
    if existing_metric is not None:
        return schemas.FitUploadFileResult(
            filename=filename,
            status="skipped",
            message=_ALREADY_IMPORTED[kind],
            activity_id=existing_metric.id,
            activity_date=parsed.ride_date,
            sport_type=parsed.sport_type,
            duration_minutes=parsed.duration_minutes,
            average_power=parsed.avg_power,
            average_heart_rate=parsed.avg_hr,
        )

    # How the session felt is the athlete's to say (ai-trainer-ops#311). An
    # effort they logged for that day stays; a new log says "not reported". The
    # 5 written here before is the top of the 1-5 scale: a file without power or
    # heart rate was priced as an all-out session, and shown as one.
    earlier = await crud.get_workout_log_by_date(db, current_user.id, parsed.ride_date)
    log = await crud.upsert_workout_log(
        db,
        current_user.id,
        parsed.ride_date,
        actual_duration_minutes=parsed.duration_minutes,
        average_power=parsed.avg_power,
        average_heart_rate=parsed.avg_hr,
        peak_power=None,
        perceived_effort=(
            earlier.perceived_effort
            if earlier is not None
            else reported_effort.EFFORT_NOT_REPORTED
        ),
        notes=f"Imported from .{kind.lower()} file: {filename}",
        completed_at=parsed.completed_at,
        sport_type=parsed.sport_type,
    )

    ai_result = (
        await analyse_fit_import(db, current_user, parsed, reply_language)
        if analyse
        else None
    )
    ftp_value = ai_result.get("estimatedFTP") if ai_result else None
    if (
        ftp_value is None
        and parsed.sport_type.lower() not in ("running", "run")
        and parsed.avg_power
    ):
        ftp_value = round(parsed.avg_power * AVG_POWER_TO_FTP_RATIO)

    # A snapshot is stamped now. One from a ride months ago would be read as
    # today's FTP, so history only feeds the chain below.
    if ftp_value is not None and analyse:
        try:
            async with db.begin_nested():
                await crud.create_athlete_metric_snapshot(
                    db,
                    current_user.id,
                    ftp=ftp_value,
                    source="fit_upload",
                )
        except Exception:
            logger.warning(
                "Failed to write metric snapshot for .fit upload", exc_info=True
            )

    # Every step a new ride takes (#248). The file's average power stands in
    # where its streams give none (the summary fallback). The chain is replayed
    # once per request, after every file (replay_after_fit_import).
    await ride_ingest.ingest_rides(
        db,
        current_user,
        [imported_activity.to_ride_input()],
        ftp=float(current_user.current_ftp or ftp_value or 0),
        policy=ride_ingest.FIT_UPLOAD,
    )

    await db.flush()
    # What the stored load rests on, so the upload can say when it is an
    # estimate (ai-trainer-ops#311).
    stored = await find_existing_import(db, current_user.id, imported_activity)
    return schemas.FitUploadFileResult(
        filename=filename,
        status="imported",
        message=_IMPORTED[kind],
        load_source=stored.tss_source if stored is not None else None,
        activity_id=log.id,
        activity_date=parsed.ride_date,
        sport_type=parsed.sport_type,
        duration_minutes=parsed.duration_minutes,
        average_power=parsed.avg_power,
        average_heart_rate=parsed.avg_hr,
    )


class FitParserUnavailable(RuntimeError):
    """The fitparse library is not installed, so no .fit file can be read."""


class UploadTooLarge(ValueError):
    """An upload went past the configured cap; it was not read to the end."""


def fit_file_parser() -> Any:
    """fitparse's ``FitFile``, or :class:`FitParserUnavailable`. Imported here:
    the library is an optional extra, and the app boots without it."""
    try:
        from fitparse import FitFile  # noqa: PLC0415
    except ImportError:
        raise FitParserUnavailable(
            "fitparse library not installed; .fit upload is unavailable"
        )
    return FitFile


# Read granularity for the capped upload read below. Large enough that a real
# .fit file costs a handful of awaits, small enough that the overshoot past the
# cap is bounded by this rather than by whatever the client sent.
_UPLOAD_CHUNK_BYTES = 256 * 1024


# What one zip may unpack to in total, in single-file limits. A FIT file is
# typically well under 1 MB, so five times the 10 MB file limit is generous for
# any honest export and keeps a hostile one from costing hundreds of MB.
_ZIP_UNPACKED_FILES_WORTH = 5


# FIT files are parsed one at a time in a worker thread. Parsing is CPU work,
# and on the single uvicorn worker an upload held the event loop for every
# athlete while it ran, a zip of a season's rides for minutes
# (ai-trainer-ops#280).
_FIT_PARSE_SLOTS = asyncio.Semaphore(1)


async def parse_fit_off_loop(
    raw: bytes, filename: str, FitFile: Any
) -> ParsedFitActivity:
    async with _FIT_PARSE_SLOTS:
        return await asyncio.to_thread(parse_fit_activity, raw, filename, FitFile)


def fit_payloads_from_zip(zip_name: str, raw: bytes) -> list[tuple[str, bytes]]:
    """The ``.fit`` files inside an uploaded ``.zip`` — Garmin's "Export Original".

    A zip is a promise about compressed size only, so everything here is
    capped against what it *unpacks* to, before reading: each member against
    the single-file limit, the whole archive against the batch, and the member
    count against the bulk file limit. A zip bomb is refused by its declared
    sizes and, for one that lies about them, by the bounded read.
    """
    limit = settings.fit_upload_max_bytes
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise ValueError(f"{zip_name} is not a readable .zip file") from exc
    with archive:
        members = [
            info
            for info in archive.infolist()
            if not info.is_dir() and info.filename.lower().endswith(".fit")
        ]
        if not members:
            raise ValueError(f"{zip_name} contains no .fit file")
        if len(members) > settings.fit_upload_bulk_max_files:
            raise ValueError(
                f"{zip_name} holds more than {settings.fit_upload_bulk_max_files} "
                ".fit files. Please split it."
            )
        total_cap = limit * _ZIP_UNPACKED_FILES_WORTH
        if sum(info.file_size for info in members) > total_cap:
            raise ValueError(f"{zip_name} unpacks to more than the upload limit")
        payloads: list[tuple[str, bytes]] = []
        unpacked = 0
        for info in members:
            if info.flag_bits & 0x1:
                raise ValueError(
                    f"{zip_name} is password-protected. Export it again without a password."
                )
            if info.file_size > limit:
                raise ValueError(
                    f"{info.filename} in {zip_name} is larger than the limit"
                )
            try:
                with archive.open(info) as member:
                    # Bounded by the cap, not the declared size: the header is
                    # the zip's own claim, and a bomb lies in it.
                    data = member.read(limit + 1)
            except (
                zipfile.BadZipFile,
                zlib.error,
                EOFError,
                RuntimeError,
                NotImplementedError,
            ) as exc:
                # A member whose stream disagrees with its header — a corrupt
                # export, or one edited to under-declare its size — or one this
                # runtime cannot open (an encryption the flag check missed, a
                # compression method zipfile lacks) is this file's failure,
                # never the request's.
                raise ValueError(f"{info.filename} in {zip_name} is damaged") from exc
            unpacked += len(data)
            if len(data) > limit or unpacked > total_cap:
                raise ValueError(f"{zip_name} unpacks to more than the upload limit")
            payloads.append((f"{zip_name} › {info.filename.rsplit('/', 1)[-1]}", data))
        return payloads


async def read_upload_capped(file: Any, filename: str) -> bytes:
    """Read *file* into memory, refusing anything over the configured cap.

    ``UploadFile.read()`` with no argument buffers the whole body whatever its
    size, so a single request could exhaust the container's memory (#682).
    Reading in chunks and stopping at the cap means an oversized upload costs
    one chunk more than the limit, not all of it.

    Raises :class:`UploadTooLarge` rather than returning a partial file: a
    truncated .fit would parse into a plausible-looking short ride. *file* is
    anything with an async ``read(size)``, as FastAPI's ``UploadFile``.
    """
    limit = settings.fit_upload_max_bytes
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_UPLOAD_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise UploadTooLarge(
                f"{filename} is larger than the {limit // (1024 * 1024)} MB "
                "limit for a .fit upload"
            )
        chunks.append(chunk)
    return b"".join(chunks)
