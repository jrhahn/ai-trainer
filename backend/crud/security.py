"""Trusted devices, recovery codes, ending a user's tokens, password resets."""

from __future__ import annotations

from datetime import datetime
from typing import Sequence

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

import models


# ---------------------------------------------------------------------------
# Second factor: trusted devices and recovery codes (#688)
# ---------------------------------------------------------------------------


async def add_trusted_device(
    db: AsyncSession,
    user_id: str,
    *,
    token_hash: str,
    expires_at: datetime,
    user_agent: str | None,
) -> models.TrustedDevice:
    """Remember a device so it can skip the second factor until *expires_at*."""
    device = models.TrustedDevice(
        user_id=user_id,
        token_hash=token_hash,
        expires_at=expires_at,
        # Truncated rather than rejected: this is a label for the revoke list,
        # and a long User-Agent is not a reason to fail a login.
        user_agent=(user_agent or "")[:255] or None,
    )
    db.add(device)
    await db.flush()
    return device


async def get_trusted_device(
    db: AsyncSession, user_id: str, token_hash: str, *, now: datetime
) -> models.TrustedDevice | None:
    """A live device row for this user, or None if absent or expired."""
    return await db.scalar(
        select(models.TrustedDevice).where(
            models.TrustedDevice.user_id == user_id,
            models.TrustedDevice.token_hash == token_hash,
            models.TrustedDevice.expires_at > now,
        )
    )


async def count_trusted_devices(
    db: AsyncSession, user_id: str, *, now: datetime
) -> int:
    return int(
        await db.scalar(
            select(func.count(models.TrustedDevice.id)).where(
                models.TrustedDevice.user_id == user_id,
                models.TrustedDevice.expires_at > now,
            )
        )
        or 0
    )


async def revoke_trusted_devices(db: AsyncSession, user_id: str) -> int:
    """Drop every trusted device for *user_id*; returns how many went.

    The only recovery when a trusted laptop is lost, and what disabling or
    re-enrolling TOTP has to call — a device trusted under an old secret must
    not survive it.
    """
    result = await db.execute(
        delete(models.TrustedDevice).where(models.TrustedDevice.user_id == user_id)
    )
    return int(result.rowcount or 0)


async def replace_recovery_codes(
    db: AsyncSession, user_id: str, code_hashes: list[str]
) -> None:
    """Swap in a fresh set, discarding any previous one."""
    await db.execute(
        delete(models.TotpRecoveryCode).where(
            models.TotpRecoveryCode.user_id == user_id
        )
    )
    for code_hash in code_hashes:
        db.add(models.TotpRecoveryCode(user_id=user_id, code_hash=code_hash))
    await db.flush()


async def list_recovery_codes(
    db: AsyncSession, user_id: str
) -> Sequence[models.TotpRecoveryCode]:
    result = await db.execute(
        select(models.TotpRecoveryCode).where(
            models.TotpRecoveryCode.user_id == user_id
        )
    )
    return result.scalars().all()


async def consume_recovery_code(
    db: AsyncSession, code: models.TotpRecoveryCode
) -> None:
    """Spend a recovery code by deleting it — they are single-use."""
    await db.delete(code)
    await db.flush()


# ---------------------------------------------------------------------------
# Token revocation (#704)
# ---------------------------------------------------------------------------


async def revoke_user_tokens(db: AsyncSession, user_id: str) -> int:
    """End every session for *user_id*; returns the generation now in force.

    Incrementing in SQL rather than reading, adding one and writing back: two
    revocations racing each other would otherwise both write the same value,
    and the later token would survive the later revocation. The increment also
    means an intermediate generation is never reachable again, so a token
    captured between two revocations stays dead.

    Trusted devices go with it. The reason to press this is that a device is
    out of your hands, and a trusted-device cookie skips the second factor for
    thirty days — leaving those behind would revoke the session but keep the
    bypass that made it easy to get one.
    """
    await db.execute(
        update(models.User)
        .where(models.User.id == user_id)
        .values(token_generation=models.User.token_generation + 1)
    )
    await revoke_trusted_devices(db, user_id)
    await db.flush()
    generation = await db.scalar(
        select(models.User.token_generation).where(models.User.id == user_id)
    )
    return int(generation or 0)


async def create_password_reset_token(
    db: AsyncSession, user_id: str, token_hash: str, expires_at: datetime
) -> models.PasswordResetToken:
    """Store a sent reset link. Every earlier link of the user goes: only the
    newest mail in the inbox is the one to follow (ai-trainer-ops#35), and a
    spent one has no use left. So the table holds at most one row per user."""
    await db.execute(
        delete(models.PasswordResetToken).where(
            models.PasswordResetToken.user_id == user_id,
        )
    )
    row = models.PasswordResetToken(
        user_id=user_id, token_hash=token_hash, expires_at=expires_at
    )
    db.add(row)
    await db.flush()
    return row


async def get_password_reset_token(
    db: AsyncSession, token_hash: str
) -> models.PasswordResetToken | None:
    return await db.scalar(
        select(models.PasswordResetToken).where(
            models.PasswordResetToken.token_hash == token_hash
        )
    )


async def claim_password_reset_token(
    db: AsyncSession, token: models.PasswordResetToken, now: datetime
) -> bool:
    """Spend *token* if nobody has; report whether this caller did.

    One conditional UPDATE, so two confirmations racing with the same link
    cannot both set a password: the second finds ``used_at`` set and changes
    no row. The user's other links go too.
    """
    result = await db.execute(
        update(models.PasswordResetToken)
        .where(
            models.PasswordResetToken.id == token.id,
            models.PasswordResetToken.used_at.is_(None),
        )
        .values(used_at=now)
    )
    if result.rowcount != 1:
        return False
    await db.execute(
        delete(models.PasswordResetToken).where(
            models.PasswordResetToken.user_id == token.user_id,
            models.PasswordResetToken.id != token.id,
        )
    )
    await db.flush()
    return True
