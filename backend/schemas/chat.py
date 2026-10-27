"""Chat messages and the notes under a reply."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel
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
    role: str
    content: str
    timestamp: str
    plan_update_count: Optional[int] = None


class ChatHistoryResponse(BaseModel):
    messages: list[ChatMessageSchema]
