"""Single-use invitation codes for a closed sign-up round (ops#108 option (b), #96).

The owner makes them on the admin page and sends a link such as
``/register?invite=K7QM-3XRP-WD``. With ``SIGNUP_INVITE_ONLY`` on, a registration
needs an unused one, and using it is one conditional UPDATE, so two sign-ups
racing for the same code cannot both get in.

A wrong code is refused before the global sign-up allowance is spent, so nobody
without a code can use up the hour's slots. Guessing is not a way in: a code
carries about 50 bits, behind the proof-of-work captcha.
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

import models

# No 0/O, 1/I/L: the code is read off a phone and sometimes typed.
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
GROUPS = (4, 4, 2)
MAX_NOTE_CHARS = 100


def new_code() -> str:
    return "-".join(
        "".join(secrets.choice(ALPHABET) for _ in range(size)) for size in GROUPS
    )


def normalize(raw: str | None) -> str | None:
    """The code as stored, from what someone typed or pasted; None when empty.

    A code is its first ten letters and digits. Whatever follows is not part of
    it: a link pasted on the way to the form picked up three letters ("chr",
    ops#165), and a messenger can glue the next word onto a URL. Ignoring the
    tail costs nothing in guessing, since the ten characters must still match.
    """
    if not raw:
        return None
    letters = re.sub(r"[^A-Z0-9]", "", raw.upper())[: sum(GROUPS)]
    if not letters:
        return None
    out, at = [], 0
    for size in GROUPS:
        out.append(letters[at : at + size])
        at += size
    return "-".join(part for part in out if part)


async def is_open(db: AsyncSession, raw: str | None) -> bool:
    """Whether ``raw`` names a code that exists and was not used."""
    code = normalize(raw)
    if code is None:
        return False
    row = await db.scalar(
        select(models.InviteCode).where(models.InviteCode.code == code)
    )
    return row is not None and row.used_at is None


async def redeem(db: AsyncSession, raw: str | None) -> bool:
    """Mark the code used, once. False when it is unknown or already used."""
    code = normalize(raw)
    if code is None:
        return False
    result = await db.execute(
        update(models.InviteCode)
        .where(models.InviteCode.code == code, models.InviteCode.used_at.is_(None))
        .values(used_at=datetime.now(timezone.utc))
    )
    return result.rowcount == 1


async def create(
    db: AsyncSession, count: int, note: str | None
) -> list[models.InviteCode]:
    label = (note or "").strip()[:MAX_NOTE_CHARS] or None
    rows = [models.InviteCode(code=new_code(), note=label) for _ in range(count)]
    db.add_all(rows)
    await db.flush()
    return rows


async def list_all(db: AsyncSession) -> list[models.InviteCode]:
    rows = await db.scalars(
        select(models.InviteCode).order_by(models.InviteCode.created_at.desc())
    )
    return list(rows)


async def revoke(db: AsyncSession, code_id: str) -> bool:
    """Delete an unused code. False when it is unknown or already used."""
    row = await db.get(models.InviteCode, code_id)
    if row is None or row.used_at is not None:
        return False
    await db.delete(row)
    await db.flush()
    return True
