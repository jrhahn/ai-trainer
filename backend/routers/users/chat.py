"""The chat history, and the flags an athlete sets on a wrong reply."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

import auth
import crud
import models
import schemas
from config import settings
from database import get_db

router = APIRouter(prefix="/users/me", tags=["users"])


@router.get("/chat", response_model=schemas.ChatHistoryResponse)
async def get_chat(
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.ChatHistoryResponse:
    messages = await crud.get_chat_messages(db, current_user.id)
    return schemas.ChatHistoryResponse(
        messages=[
            schemas.ChatMessageSchema.model_validate(m, from_attributes=True)
            for m in messages
        ]
    )


@router.post("/chat", response_model=schemas.ChatMessageSchema)
async def add_chat_message(
    body: schemas.ChatMessageRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.ChatMessageSchema:
    message = await crud.create_chat_message(
        db,
        current_user.id,
        role=body.role,
        content=body.content,
        timestamp=body.timestamp,
        plan_update_count=body.plan_update_count,
    )
    return schemas.ChatMessageSchema.model_validate(message, from_attributes=True)


# A tester flagging everything they read is still telling us something; a
# script posting in a loop is not. ponytail: a per-user daily count, a real
# rate limiter if this is ever abused.
WRONG_FLAGS_PER_DAY = 50


@router.post("/wrong-flags", status_code=status.HTTP_201_CREATED, response_model=dict)
async def flag_wrong(
    body: schemas.WrongFlagCreate,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> dict:
    """ "This is wrong" on a coach reply or a planned day (ai-trainer-ops#94)."""
    since = datetime.now(timezone.utc) - timedelta(days=1)
    if (
        await crud.count_wrong_flags_since(db, current_user.id, since)
        >= WRONG_FLAGS_PER_DAY
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="That is a lot of flags for one day. Thank you; write to us for the rest.",
        )
    flag = await crud.create_wrong_flag(
        db,
        current_user.id,
        target=body.target,
        ref=body.ref,
        excerpt=body.excerpt,
        note=body.note,
        git_sha=settings.git_sha or "unknown",
    )
    return {"id": flag.id}


@router.delete("/chat")
async def clear_chat(
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> dict:
    await crud.delete_chat_messages(db, current_user.id)
    return {"status": "deleted"}
