"""a stored trip without the bike rules out rides only

ai-trainer-ops#136. A trip said with the bike at home was stored as "no
training" on every day, so the walks and hikes the athlete planned could not go
into the plan. Such days are now "no_riding". This moves the days already
stored: a day of a stated range whose message keeps the bike at home and names
something done instead, or does not rule out training altogether.

Revision ID: 20261118_000001
Revises: 20261107_000001
Create Date: 2026-11-18 00:00:01
"""

from __future__ import annotations

import re

import sqlalchemy as sa

from alembic import op

revision = "20261118_000001"
down_revision = "20261107_000001"
branch_labels = None
depends_on = None

# The reason a day of a stated range was stored with, before and after.
RANGE_REASON = "Athlete said they are away and cannot train."
NO_RIDING = "no_riding"
NO_RIDING_REASON = (
    "Athlete is away without the bike: no rides on this day. Sessions without "
    "the bike (walking, hiking, strength) can still be planned."
)

# A snapshot of domain.availability as of this revision (review of #1334): a
# later change to the live patterns must not change what this migration does.
_NO_BIKE = re.compile(
    r"\b(?:kein(?:e|en)?|ohne|nicht\s+mit(?:\s+dem|\s+meinem)?)\s+"
    r"(?:rad|fahrrad|rennrad|renner|bike|gravelbike)\b"
    r"|\b(?:rad|fahrrad|rennrad|bike)\s+(?:ist\s+|bleibt\s+)?nicht\s+(?:dabei|mit)\b"
    r"|\b(?:rad|fahrrad|rennrad|bike)\s+bleibt\s+(?:zu\s*hause|daheim)\b"
    r"|\b(?:no|without(?:\s+my|\s+the|\s+a)?)\s+bike\b"
    r"|\bnot\s+(?:taking|bringing)\s+(?:my|the|a)\s+bike\b"
    r"|\bbike\s+stays\s+(?:at\s+)?home\b"
)
_INSTEAD_OF_RIDING = re.compile(
    r"(?<!nicht\s)(?<!kein\s)(?<!no\s)(?<!not\s)"
    r"\b(?:gehen|spazieren(?:\s*gehen)?|spaziergänge?n?|wandern|wanderung(?:en)?"
    r"|laufen|joggen|kraft(?:training)?|walking|walks?|hiking|hikes?|running|runs?"
    r"|strength(?:\s+training)?|gym)\b"
)
_NO_TRAINING_PLAIN = re.compile(
    r"\b(?:kein(?:e|en)?|ohne)\s+training\b|\bno\s+training\b"
    r"|\bnicht\s+trainieren\b|\bcan(?:not|'t)\s+train\b"
)
_SENTENCE = re.compile(r"(?:(?<=\d)\.|[^.!?;])+[.!?;]?")


def says_rides_only(text: str) -> bool:
    plain = text.replace("\u2019", "'").replace("\u2018", "'")
    normalized = " ".join(plain.casefold().split())
    for match in _SENTENCE.finditer(normalized):
        sentence = match.group()
        if _NO_BIKE.search(sentence) and (
            _INSTEAD_OF_RIDING.search(sentence)
            or not _NO_TRAINING_PLAIN.search(sentence)
        ):
            return True
    return False


_constraints = sa.table(
    "athlete_availability_constraints",
    sa.column("id", sa.String),
    sa.column("constraint_type", sa.String),
    sa.column("reason", sa.Text),
    sa.column("source", sa.Text),
)


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.select(_constraints.c.id, _constraints.c.source).where(
            _constraints.c.constraint_type == "no_training",
            _constraints.c.reason == RANGE_REASON,
        )
    ).all()
    ids = [row.id for row in rows if says_rides_only(row.source or "")]
    if ids:
        conn.execute(
            _constraints.update()
            .where(_constraints.c.id.in_(ids))
            .values(constraint_type=NO_RIDING, reason=NO_RIDING_REASON)
        )


def downgrade() -> None:
    # The code before this revision knows no "no_riding": the day is blocked
    # again, as it was.
    op.get_bind().execute(
        _constraints.update()
        .where(_constraints.c.constraint_type == NO_RIDING)
        .values(constraint_type="no_training", reason=RANGE_REASON)
    )
