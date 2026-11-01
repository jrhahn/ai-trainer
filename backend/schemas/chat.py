"""Chat messages and the notes under a reply."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field
from schemas.base import CamelModel
from schemas.profile import OnboardingSport, TrainingWeekday


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


# ---------------------------------------------------------------------------
# The offers under a reply (ops#43, #71, #175), stored with it (ops#338 T1)
# ---------------------------------------------------------------------------


class ProfileProposalSchema(CamelModel):
    """Hours, training days, sports, an FTP, the level or the max heart rate the
    athlete said in chat, to confirm (ops#43, #129, #202).

    Only the fields that differ from the profile are set.
    """

    weekly_hours: Optional[float] = None
    training_days: Optional[list[TrainingWeekday]] = None
    sports: Optional[list[OnboardingSport]] = None
    ftp: Optional[int] = None
    fitness_level: Optional[Literal["beginner", "intermediate", "advanced"]] = None
    max_heart_rate: Optional[int] = None
    standing_day_swap: bool = False
    """The training days swap in a day the athlete generally prefers
    (ai-trainer-ops#204): a lasting change, which the offer says."""


class RaceProposalSchema(CamelModel):
    """The athlete said there is no race: offer to clear the profile's (ops#71)."""

    race_date: Optional[str] = None
    race_description: Optional[str] = None
    calendar_races: int = 0


class RaceMovedFromSchema(CamelModel):
    """The race a moved race replaces: the profile's, or one in the calendar."""

    date: str
    kind: Literal["profile", "calendar"]
    event_id: Optional[str] = None


class RaceOfferSchema(CamelModel):
    """A race the athlete said is coming, to add to the calendar (ai-trainer-ops#175)."""

    # ISO date, resolved on the server from what the athlete wrote.
    date: str
    # When the athlete says the race moves, the race it moves from, so the offer
    # moves it rather than adding a second one (#175, comment 5111).
    replaces: Optional[RaceMovedFromSchema] = None


OfferKind = Literal["raceOffer", "raceProposal", "profileProposal"]
OfferOutcome = Literal["accepted", "dismissed"]


class ChatOffersSchema(CamelModel):
    """The offers under a stored reply and what the athlete answered to each.

    Stored as the response gave them, so a reload shows the same buttons; an
    offer in ``resolved`` was answered and is not offered again
    (ai-trainer-ops#338 T1).
    """

    race_offer: Optional[RaceOfferSchema] = None
    race_proposal: Optional[RaceProposalSchema] = None
    profile_proposal: Optional[ProfileProposalSchema] = None
    resolved: dict[OfferKind, OfferOutcome] = {}


class OfferResolveRequest(CamelModel):
    """The athlete accepted or dismissed one offer under a reply."""

    outcome: OfferOutcome


class ChatMessageSchema(CamelModel):
    # The row's id; an assistant reply's offers are answered by it (#338 T1).
    id: Optional[str] = None
    role: str
    content: str
    timestamp: str
    plan_update_count: Optional[int] = None
    # The app's notes under an assistant reply; never part of ``content``.
    notes: list[ReplyNoteSchema] = []
    # The coach's sentence per day the reply changed; never part of ``content``.
    day_summaries: list[DaySummarySchema] = []
    # The offers under the reply, with the athlete's answers; None when none.
    offers: Optional[ChatOffersSchema] = None


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
