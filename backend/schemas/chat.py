"""Chat messages and the notes under a reply."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field
from schemas.base import CamelModel


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


class ReplyNoteSchema(CamelModel):
    """A note the app itself adds under a coach reply (ai-trainer-ops#262)."""

    kind: str
    text: str


class DaySummarySchema(CamelModel):
    """The coach's one sentence on a day its reply changed (ai-trainer-ops#266)."""

    date: str
    summary: str


class ChatMessageSchema(CamelModel):
    role: str
    content: str
    timestamp: str
    plan_update_count: Optional[int] = None
    # The app's notes under an assistant reply; never part of ``content``.
    notes: list[ReplyNoteSchema] = []
    # The coach's sentence per day the reply changed; never part of ``content``.
    day_summaries: list[DaySummarySchema] = []


class ChatMessageRequest(CamelModel):
    """A message written into the athlete's own history.

    Bounded like everything else that later reaches a prompt or the coach
    review: an unbounded role or body was a way to plant any text of any size
    in the history (review of 2026-11-01, ai-trainer-ops#338).
    """

    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)
    timestamp: str = Field(max_length=64)
    plan_update_count: Optional[int] = Field(default=None, ge=0, le=1000)


class ChatHistoryResponse(BaseModel):
    messages: list[ChatMessageSchema]
