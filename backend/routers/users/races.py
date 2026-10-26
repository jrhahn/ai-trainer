"""The race calendar and the goals.

A change to the calendar is kept in step with the profile's next race and with
the race section of the coach memory.
"""

import re

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

import auth
import crud
import models
import schemas
from database import get_db
from services.dates import app_today_iso, request_timezone

router = APIRouter(prefix="/users/me", tags=["users"])


_RACE_MEMORY_HEADING = "Race calendar:"


def _format_race_event_for_memory(event: models.RaceEvent) -> str:
    time_part = f" at {event.start_time}" if event.start_time else ""
    return (
        f"{event.date}{time_part}: {event.distance_km:g} km, "
        f"{event.elevation_m} m climbing"
    )


def _race_events_memory_section(events: list[models.RaceEvent]) -> str:
    if not events:
        return ""
    lines = [_RACE_MEMORY_HEADING]
    lines.extend(f"- {_format_race_event_for_memory(event)}" for event in events)
    return "\n".join(lines)


def _merge_race_events_into_memory(memory: str, events: list[models.RaceEvent]) -> str:
    cleaned = re.sub(
        rf"(?:\n\n)?{re.escape(_RACE_MEMORY_HEADING)}\n(?:- .*(?:\n|$))*",
        "",
        memory or "",
    ).strip()
    section = _race_events_memory_section(events)
    return "\n\n".join(part for part in (cleaned, section) if part)


def _sync_profile_next_race(user: models.User, events: list[models.RaceEvent]) -> None:
    today = app_today_iso()
    upcoming = [event for event in events if event.date >= today]
    if not upcoming:
        user.race_date = None
        user.race_description = None
        return

    next_event = sorted(
        upcoming, key=lambda event: (event.date, event.start_time or "")
    )[0]
    user.training_goal = "race"
    user.race_date = next_event.date
    user.race_description = (
        f"Calendar race: {next_event.distance_km:g} km with "
        f"{next_event.elevation_m} m climbing"
    )


async def _sync_race_context(
    db: AsyncSession, current_user: models.User
) -> list[models.RaceEvent]:
    events = await crud.get_race_events(db, current_user.id)
    _sync_profile_next_race(current_user, events)
    # Lock the row for the read-modify-write so a concurrent coach-memory edit
    # (e.g. PUT /coach-memory) can't be clobbered by this merge (#346).
    existing_memory = await crud.get_coach_memory(db, current_user.id, for_update=True)
    merged_memory = _merge_race_events_into_memory(
        existing_memory.memory if existing_memory is not None else "",
        events,
    )
    await crud.upsert_coach_memory(db, current_user.id, merged_memory)
    return events


@router.get("/race-events", response_model=schemas.RaceEventsResponse)
async def get_race_events(
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.RaceEventsResponse:
    events = await crud.get_race_events(db, current_user.id)
    return schemas.RaceEventsResponse(
        events=[
            schemas.RaceEventResponse.model_validate(event, from_attributes=True)
            for event in events
        ]
    )


@router.post("/race-events", response_model=schemas.RaceEventResponse)
async def create_race_event(
    body: schemas.RaceEventRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.RaceEventResponse:
    event = await crud.create_race_event(
        db,
        current_user.id,
        date=body.date,
        start_time=body.start_time,
        distance_km=body.distance_km,
        elevation_m=body.elevation_m,
    )
    await _sync_race_context(db, current_user)
    return schemas.RaceEventResponse.model_validate(event, from_attributes=True)


@router.put("/race-events/{event_id}", response_model=schemas.RaceEventResponse)
async def update_race_event(
    event_id: str,
    body: schemas.RaceEventRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.RaceEventResponse:
    event = await crud.get_race_event(db, current_user.id, event_id)
    if event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Race event not found"
        )
    updated = await crud.update_race_event(
        db,
        event,
        date=body.date,
        start_time=body.start_time,
        distance_km=body.distance_km,
        elevation_m=body.elevation_m,
    )
    await _sync_race_context(db, current_user)
    return schemas.RaceEventResponse.model_validate(updated, from_attributes=True)


@router.delete("/race-events/{event_id}")
async def delete_race_event(
    event_id: str,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> dict:
    event = await crud.get_race_event(db, current_user.id, event_id)
    if event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Race event not found"
        )
    await crud.delete_race_event(db, event)
    await _sync_race_context(db, current_user)
    return {"status": "deleted"}


@router.delete("/goals/{goal_id}")
async def remove_goal(
    goal_id: str,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> dict:
    """Undo a goal the chat captured (ai-trainer-ops#65, owner decision Q20)."""
    if await crud.remove_goal(db, current_user.id, goal_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Goal not found"
        )
    return {"status": "removed"}


@router.post("/race/clear")
async def clear_profile_race(
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> dict:
    """Clear the race the athlete confirmed they no longer have.

    The confirm step the owner chose for "I have no race" (ai-trainer-ops#71,
    #63 Q21). The profile's race comes back from any upcoming calendar race on
    the next race-event sync, so upcoming calendar races go too; past ones stay
    as history. The sync then rewrites the coach memory's race section.
    """
    # The athlete's day, the same one the chat used to count the races it named.
    today = app_today_iso(timezone_name=request_timezone(request))
    removed = 0
    for event in await crud.get_race_events(db, current_user.id):
        if event.date >= today:
            await crud.delete_race_event(db, event)
            removed += 1
    await _sync_race_context(db, current_user)
    current_user.race_date = None
    current_user.race_description = None
    if current_user.training_goal == "race":
        current_user.training_goal = "general_fitness"
    await db.flush()
    return {"status": "cleared", "removedRaceEvents": removed}
