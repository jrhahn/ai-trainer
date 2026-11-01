"""The athlete's own AI key: its status, saving, deleting and testing it."""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

import auth
import crud
import models
import schemas
from config import settings
from database import get_db
from services import llm as llm_service
from routers.dependencies import enforce_ai_rate_limit
from services.key_hint import key_hint

router = APIRouter(prefix="/users/me", tags=["users"])

logger = logging.getLogger(__name__)


async def _ai_key_status(
    db: AsyncSession, user: models.User
) -> schemas.AIKeyStatusSchema:
    """The athlete's key situation, and how much of the free allowance is used.

    The allowance (ai-trainer-ops#79) applies only while the owner's key pays;
    with their own key, or with no budget configured, it is ``None``.
    """
    limited = settings.ai_token_budget > 0 and llm_service.pays_with_admin_key(user)
    used = (
        await crud.sum_user_tokens_since(
            db,
            user.id,
            datetime.now(timezone.utc)
            - timedelta(days=settings.ai_token_budget_window_days),
        )
        if limited
        else 0
    )
    return schemas.AIKeyStatusSchema(
        provider=user.ai_provider or "gemini",
        has_openai_key=bool(user.user_openai_api_key),
        has_gemini_key=bool(user.user_gemini_api_key),
        openai_key_hint=key_hint(user.user_openai_api_key),
        gemini_key_hint=key_hint(user.user_gemini_api_key),
        openai_model=settings.openai_coach_model,
        gemini_model=settings.gemini_coach_model,
        key_required=llm_service.user_must_supply_own_key(user),
        allowance_tokens=settings.ai_token_budget if limited else None,
        allowance_used=used,
        allowance_window_days=settings.ai_token_budget_window_days,
    )


@router.get("/ai-key/status", response_model=schemas.AIKeyStatusSchema)
async def get_ai_key_status(
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AIKeyStatusSchema:
    return await _ai_key_status(db, current_user)


@router.put("/ai-key", response_model=schemas.AIKeyStatusSchema)
async def save_ai_key(
    body: schemas.AIKeySaveRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AIKeyStatusSchema:
    if body.provider == "openai":
        current_user.user_openai_api_key = body.api_key
    elif body.provider == "gemini":
        current_user.user_gemini_api_key = body.api_key
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown provider: {body.provider}",
        )
    current_user.ai_provider = body.provider
    await db.commit()
    # Recomputed rather than assumed False: the key just saved may be for a
    # provider this deployment cannot reach, and the caller is about to decide
    # whether onboarding may continue.
    return await _ai_key_status(db, current_user)


@router.delete("/ai-key", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ai_key(
    provider: str = Query(...),
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> Response:
    if provider == "openai":
        current_user.user_openai_api_key = None
    elif provider == "gemini":
        current_user.user_gemini_api_key = None
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown provider: {provider}",
        )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/ai-key/test",
    # Rate-limited like every route that calls a provider: unlimited, it let
    # anyone with an account check stolen keys from this server's address
    # (review of 2026-11-01, ai-trainer-ops#338).
    dependencies=[Depends(enforce_ai_rate_limit)],
)
async def test_ai_key(
    body: schemas.AIKeySaveRequest,
    current_user: models.User = Depends(auth.get_current_user),
) -> dict:
    """Validate an API key without storing it."""
    token = llm_service.set_user_ai_keys({body.provider: body.api_key})
    try:
        provider_instance = llm_service.get_provider(
            body.provider, task=llm_service.TASK_CLASSIFY
        )
        await provider_instance.chat(
            "You are a connectivity test.", "Reply with the single word: ok"
        )
        return {"ok": True}
    except llm_service.AIKeyNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )
    except Exception as exc:
        # Log the exception type only: the provider's message can echo a
        # fragment of the key being tested (ops#132).
        logger.warning(
            "AI key validation failed for user %s: %s",
            current_user.id,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Key validation failed. Check that the key is correct and has the required permissions.",
        )
    finally:
        llm_service.reset_user_ai_keys(token)
