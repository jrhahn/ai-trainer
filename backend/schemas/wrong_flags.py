"""The flags an athlete sets on a wrong reply."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import Field, field_validator
from schemas.base import CamelModel


# ---------------------------------------------------------------------------
# "This is wrong" (ai-trainer-ops#94)
# ---------------------------------------------------------------------------

# What the client sends of a long reply. The admin needs to see what was meant,
# not the whole of a long answer twice.
WRONG_FLAG_EXCERPT_MAX = 4000


WRONG_FLAG_NOTE_MAX = 1000


class WrongFlagCreate(CamelModel):
    target: Literal["reply", "day"]
    ref: str = Field(min_length=1, max_length=64)
    excerpt: str = Field(min_length=1, max_length=WRONG_FLAG_EXCERPT_MAX)
    note: Optional[str] = Field(default=None, max_length=WRONG_FLAG_NOTE_MAX)

    @field_validator("note", mode="before")
    @classmethod
    def _strip_note(cls, value: Any) -> Any:
        # Whitespace around a note is not part of it, nor of its length.
        return (value.strip() or None) if isinstance(value, str) else value


class WrongFlagOut(CamelModel):
    id: str
    created_at: datetime
    email: str
    target: str
    ref: str
    excerpt: str
    note: Optional[str] = None
    git_sha: str
    handled_at: Optional[datetime] = None
    handled_note: Optional[str] = None


class WrongFlagsResponse(CamelModel):
    flags: list[WrongFlagOut]


WRONG_FLAG_HANDLED_NOTE_MAX = 200


class WrongFlagHandledRequest(CamelModel):
    """Mark a flag answered or turned into an issue, or take that back (ops#96)."""

    handled: bool
    note: Optional[str] = Field(default=None, max_length=WRONG_FLAG_HANDLED_NOTE_MAX)

    @field_validator("note", mode="before")
    @classmethod
    def _strip_note(cls, value: Any) -> Any:
        return (value.strip() or None) if isinstance(value, str) else value
