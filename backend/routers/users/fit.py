""".fit, .gpx and .tcx uploads: one file, or many at once.

The parsing and the storing live in services/fit_import.py (ai-trainer-ops#251)
and, for GPX and TCX, services/track_import.py (#311).
"""

import asyncio
from datetime import date as date_type

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

import auth
import models
import schemas
from config import settings
from database import get_db
from routers.dependencies import (
    consume_ai_allowance,
    enforce_ai_rate_limit,
    fit_file_parser_or_503,
    read_upload_capped,
    set_user_ai_keys,
)
from services import summary_pipeline
from services.athlete_language import interface_language
from services import fit_import, track_import
from services.dates import app_today

router = APIRouter(prefix="/users/me", tags=["users"])


async def _fit_reply_language(
    db: AsyncSession, current_user: models.User, request: Request
) -> str | None:
    """Their chat words first, the interface next (ai-trainer-ops#121, #258)."""
    return await summary_pipeline.athlete_language_of(
        db, current_user.id
    ) or interface_language(request)


# GPX and TCX were refused until ai-trainer-ops#311: they carry a track and
# maybe heart rate, not a device's summary. They come in now with a load the
# chain estimates from what they do carry, and the upload says what that was.
_SINGLE_FORMATS = (".fit", *track_import.TRACK_FORMATS)
_BULK_FORMATS = (*_SINGLE_FORMATS, ".zip")


async def _parse_upload(
    data: bytes, filename: str, FitFile: object
) -> fit_import.ParsedFitActivity:
    if filename.lower().endswith(track_import.TRACK_FORMATS):
        return await asyncio.to_thread(
            track_import.parse_track_activity, data, filename
        )
    return await fit_import.parse_fit_off_loop(data, filename, FitFile)


@router.post(
    "/upload-fit",
    response_model=schemas.FitUploadResponse,
    # The one LLM-spending route outside the /ai router: it runs
    # ``api:analyse-fit-import`` per upload, so it needs the same limit (#676).
    # set_user_ai_keys as well as the limit (#693): without a BYOK context
    # ``llm.get_provider`` takes the scheduler branch and spends the *global*
    # key unconditionally, so these routes kept billing the owner after
    # ALLOW_ADMIN_AI_KEY_FALLBACK was turned off.
    dependencies=[Depends(set_user_ai_keys), Depends(enforce_ai_rate_limit)],
)
async def upload_fit_file(
    file: UploadFile,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.FitUploadResponse:
    """Ingest one .fit, .gpx or .tcx file and store workout plus normalized ride metrics."""
    FitFile = fit_file_parser_or_503()
    filename = file.filename or ""
    if not filename.lower().endswith(_SINGLE_FORMATS):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only .fit, .gpx and .tcx files are accepted",
        )

    raw = await read_upload_capped(file, filename or "upload")
    try:
        parsed = await _parse_upload(raw, filename, FitFile)
        result = await fit_import.store_fit_import(
            db,
            current_user,
            filename,
            parsed,
            set(),
            reply_language=await _fit_reply_language(db, current_user, request),
        )
        if result.status == "imported":
            await fit_import.replay_after_fit_import(db, current_user)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    return schemas.FitUploadResponse(
        status="ok" if result.status == "imported" else result.status,
        activity_id=result.activity_id or "",
        sport_type=result.sport_type or parsed.sport_type,
        duration_minutes=result.duration_minutes or parsed.duration_minutes,
        average_power=result.average_power,
        average_heart_rate=result.average_heart_rate,
        load_source=result.load_source,
    )


# A ride older than this is history: it counts for load and fitness, but the
# model's read of it ("last ride" feedback, an FTP estimate) would describe a
# day that is gone (ai-trainer-ops#157).
FIT_ANALYSIS_RECENT_DAYS = 14


def _is_recent_ride(parsed: fit_import.ParsedFitActivity, today: date_type) -> bool:
    try:
        ride_day = date_type.fromisoformat(str(parsed.ride_date)[:10])
    except ValueError:
        return True
    return (today - ride_day).days <= FIT_ANALYSIS_RECENT_DAYS


_RATE_LIMITED_PARTWAY = (
    "Not imported: AI rate limit reached partway through the batch. Retry these "
    "files shortly."
)


@router.post(
    "/upload-fit/bulk",
    response_model=schemas.FitBulkUploadResponse,
    # The second LLM-spending route outside the /ai router, and the one that
    # was missed when #676 wired the first: every file here runs
    # ``api:analyse-fit-import`` through ``fit_import.store_fit_import``, so a single
    # unlimited request was worth an unbounded number of provider calls (#682).
    # set_user_ai_keys as well as the limit (#693): without a BYOK context
    # ``llm.get_provider`` takes the scheduler branch and spends the *global*
    # key unconditionally, so these routes kept billing the owner after
    # ALLOW_ADMIN_AI_KEY_FALLBACK was turned off.
    dependencies=[Depends(set_user_ai_keys), Depends(enforce_ai_rate_limit)],
)
async def upload_fit_files_bulk(
    request: Request,
    files: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.FitBulkUploadResponse:
    """Import multiple .fit, .gpx or .tcx files, reporting success, duplicate, and failure per file."""
    FitFile = fit_file_parser_or_503()
    if len(files) > settings.fit_upload_bulk_max_files:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=(
                f"At most {settings.fit_upload_bulk_max_files} .fit files can be "
                "uploaded at once. Please split the batch."
            ),
        )
    results: list[schemas.FitUploadFileResult] = []
    seen_source_ids: set[int] = set()
    # Once for the batch, not once per file (review of #1128).
    reply_language = await _fit_reply_language(db, current_user, request)
    today = app_today(timezone_name=current_user.timezone_name)

    def failed(name: str, message: str) -> schemas.FitUploadFileResult:
        return schemas.FitUploadFileResult(
            filename=name, status="failed", message=message
        )

    # The route dependency charged the request once, which covers the first
    # payload. Every further one is another LLM call, so it pays for itself —
    # otherwise the batch size would be a way to buy AI calls at a flat rate of
    # one. A zip's members count one by one, for the same reason. Limiting
    # stops the batch rather than failing it: what was already imported stays
    # imported, and the response says which files were not attempted.
    first_payload = True
    rate_limited = False

    for index, file in enumerate(files):
        filename = file.filename or "unnamed"
        # No short cut on ``rate_limited`` here: a ride older than
        # ``FIT_ANALYSIS_RECENT_DAYS`` costs nothing, so a spent allowance must
        # not refuse the history behind it (review of app PR #978). Whether a
        # file costs anything is known only once it is parsed.
        lowered = filename.lower()
        if not lowered.endswith(_BULK_FORMATS):
            results.append(
                failed(
                    filename,
                    "Only .fit, .gpx and .tcx files, or a .zip of .fit files, are accepted",
                )
            )
            continue

        try:
            # One oversized file fails its own entry rather than the batch —
            # the same treatment an unparseable file already gets.
            raw = await read_upload_capped(file, filename)
            payloads = (
                await asyncio.to_thread(fit_import.fit_payloads_from_zip, filename, raw)
                if lowered.endswith(".zip")
                else [(filename, raw)]
            )
        except HTTPException as exc:
            if exc.status_code != status.HTTP_413_CONTENT_TOO_LARGE:
                raise
            results.append(failed(filename, str(exc.detail)))
            continue
        except ValueError as exc:
            results.append(failed(filename, str(exc)))
            continue

        for name, data in payloads:
            try:
                parsed = await _parse_upload(data, name, FitFile)
            except ValueError as exc:
                results.append(failed(name, str(exc)))
                continue
            # Only a recent ride gets the model's read, so only a recent ride
            # pays for one (ai-trainer-ops#157). An export of past months used
            # to stop after ten files on the burst limit, every one of them
            # analysed for a "last ride" feedback none of them was.
            analyse = _is_recent_ride(parsed, today)
            if analyse and rate_limited:
                results.append(failed(name, _RATE_LIMITED_PARTWAY))
                continue
            if analyse and not first_payload:
                try:
                    consume_ai_allowance(current_user.id)
                except HTTPException as exc:
                    if exc.status_code != status.HTTP_429_TOO_MANY_REQUESTS:
                        raise
                    rate_limited = True
                    results.append(failed(name, _RATE_LIMITED_PARTWAY))
                    continue
            if analyse:
                first_payload = False
            try:
                result = await fit_import.store_fit_import(
                    db,
                    current_user,
                    name,
                    parsed,
                    seen_source_ids,
                    analyse=analyse,
                    reply_language=reply_language,
                )
            except ValueError as exc:
                result = failed(name, str(exc))
            results.append(result)

    imported = sum(1 for result in results if result.status == "imported")
    # Once per request: the app sends a large selection in parts of
    # ``fit_upload_bulk_max_files``, and each part replays the chain once. One
    # indexed read and a pass over the loads, so four parts cost four.
    if imported:
        await fit_import.replay_after_fit_import(db, current_user)
    skipped = sum(1 for result in results if result.status == "skipped")
    failed = sum(1 for result in results if result.status == "failed")
    return schemas.FitBulkUploadResponse(
        status="ok" if failed == 0 else "partial",
        total=len(results),
        imported=imported,
        skipped=skipped,
        failed=failed,
        files=results,
    )
