"""relabel non-cycling activities that were stored as unclassifiable rides

Every activity without a usable power stream was stored as
``ride_purpose = 'unknown'`` with confidence ``low`` and the reason
"Insufficient stream data to classify ride reliably." — including the ones that
were never bike rides. On prod that is 248 strength, yoga, hiking and climbing
sessions, and it is what made the coach ask a 58-minute gym session what its
intervals were (#578).

The classifier now consults the sport type first. This backfill applies the same
rule to the rows already stored, so history stops carrying a label that was never
true. Only rows that are ``unknown``/NULL are touched: a real classification and
anything the athlete edited stay exactly as they are.

Revision ID: 20260814_000001
Revises: 20260813_000001
Create Date: 2026-08-07 00:00:01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

from services.activity_identity import non_cycling_classification
from services.analysis import build_rule_based_summary


revision = "20260814_000001"
down_revision = "20260813_000001"
branch_labels = None
depends_on = None

_UNKNOWN_REASON = "Insufficient stream data to classify ride reliably."

_SELECT = sa.text(
    "SELECT id, sport_type, duration_seconds, normalized_power_w, tss, ride_purpose "
    "FROM ride_metrics"
)

_UPDATE = sa.text(
    "UPDATE ride_metrics SET ride_purpose = :purpose, "
    "classification_confidence = :confidence, classification_reason = :reason, "
    "summary = :summary WHERE id = :row_id"
)


def _table_exists(name: str) -> bool:
    return name in sa_inspect(op.get_bind()).get_table_names()


def _summary_for(purpose: str, row: sa.engine.Row) -> str:
    return build_rule_based_summary(
        purpose,
        float(row.duration_seconds or 0),
        float(row.normalized_power_w) if row.normalized_power_w else None,
        float(row.tss) if row.tss is not None else None,
        [],
    )


def upgrade() -> None:
    if not _table_exists("ride_metrics"):
        return

    bind = op.get_bind()
    updates: list[dict[str, object]] = []
    for row in bind.execute(_SELECT):
        if row.ride_purpose not in (None, "unknown"):
            continue
        classification = non_cycling_classification(row.sport_type)
        if classification is None:
            continue
        purpose, confidence, reason = classification
        updates.append(
            {
                "row_id": row.id,
                "purpose": purpose,
                "confidence": confidence,
                "reason": reason,
                "summary": _summary_for(purpose, row),
            }
        )

    for update in updates:
        bind.execute(_UPDATE, update)


def downgrade() -> None:
    if not _table_exists("ride_metrics"):
        return

    bind = op.get_bind()
    reverts: list[dict[str, object]] = []
    for row in bind.execute(_SELECT):
        classification = non_cycling_classification(row.sport_type)
        # Only revert rows this migration itself could have written: the label
        # still has to be the one the rule produces for that sport.
        if classification is None or classification[0] != row.ride_purpose:
            continue
        reverts.append(
            {
                "row_id": row.id,
                "purpose": "unknown",
                "confidence": "low",
                "reason": _UNKNOWN_REASON,
                "summary": _summary_for("unknown", row),
            }
        )

    for revert in reverts:
        bind.execute(_UPDATE, revert)
