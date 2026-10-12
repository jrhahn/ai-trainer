"""Public tools: no account, nothing stored, no model call (ai-trainer-ops#25)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status

import schemas
from config import settings
from routers.dependencies import _GLOBAL_KEY, _enforce
from routers.users import (
    _fit_file_parser_or_503,
    _parse_fit_activity,
    _read_upload_capped,
)
from services.power_curve import power_duration
from services.rate_limit import SlidingWindowLimiter, Window

router = APIRouter(prefix="/tools", tags=["tools"])

power_curve_limiter = SlidingWindowLimiter()
# One parse at a time. Parsing a large .fit is seconds of CPU; the rate limit
# bounds how many arrive, this bounds how many run at once.
_one_at_a_time = asyncio.Semaphore(1)


def enforce_power_curve_rate_limit() -> None:
    """Bound the anonymous tool deployment-wide.

    Global rather than per client: uvicorn runs without ``--proxy-headers``,
    so every request arrives from Traefik's address and there is no client to
    key on. A flood then locks the tool for everyone for the window, which is
    the acceptable failure for a free tool; the coach is not affected.
    """
    _enforce(
        power_curve_limiter,
        _GLOBAL_KEY,
        (
            Window(settings.power_curve_rate_limit_burst, 60),
            Window(settings.power_curve_rate_limit_hourly, 3600),
        ),
        "The free curve tool is busy. Please try again in a few minutes.",
    )


@router.post(
    "/power-curve",
    response_model=schemas.PowerCurveResponse,
    dependencies=[Depends(enforce_power_curve_rate_limit)],
)
async def power_curve(file: UploadFile) -> schemas.PowerCurveResponse:
    """The power-duration curve of one uploaded ride. Nothing is kept."""
    FitFile = _fit_file_parser_or_503()
    filename = file.filename or ""
    if not filename.lower().endswith(".fit"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only .fit files are accepted",
        )
    raw = await _read_upload_capped(file, filename)
    async with _one_at_a_time:
        try:
            parsed = await asyncio.to_thread(_parse_fit_activity, raw, filename, FitFile)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        watts = parsed.streams.get("watts", {}).get("data") or []
        times = parsed.streams.get("time", {}).get("data") or []
        if not watts:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="This file has no power data. The curve needs a power meter.",
            )
        result = await asyncio.to_thread(power_duration, watts, times)
    if not result["points"]:
        # No timeline to window over, or a ride under five seconds of power.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="This file has too little power data with timestamps for a curve.",
        )
    return schemas.PowerCurveResponse(
        duration_seconds=parsed.duration_seconds, **result
    )
