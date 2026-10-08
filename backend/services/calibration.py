"""Does the coach's 0.6 come true 60% of the time? (ai-trainer-ops#27)

``prediction_evaluation`` scores each prediction ``correct`` or ``incorrect``.
This module asks the question across all of them: bucket the predictions by the
confidence the coach *stated*, and compare each bucket's mean confidence with
how often it actually came true. A calibrated coach sits on the diagonal.

Two summary numbers, both standard so they mean what a reader expects:

* **Brier score** — the mean squared gap between stated confidence and the
  outcome (1 for a hit, 0 for a miss). 0 is perfect; always saying 0.5 scores
  0.25, so anything above that is worse than not committing at all.
* **Expected calibration error** — the bucket-size-weighted mean gap between
  a bucket's confidence and its hit rate. It answers the issue's question
  directly: how far off, on average, is the number the athlete sees.

Only ``stated_confidence`` is measured. The ``confidence`` column moves with the
outcome (evaluation nudges it ±0.2) and with the athlete's edits, so binning it
measures the nudge rather than the coach — see the migration that added the
column. Rows from before it have no stated value; :func:`reconstructed_pairs`
estimates one for them, and nothing here ever mixes the two.

This module measures. What ``confidence`` *should* mean — whether it is meant
to be a frequency at all — is the decision #27 is really about, and it is not
made here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models

DEFAULT_BIN_WIDTH = 0.1
_EVALUATED = ("correct", "incorrect")


@dataclass(frozen=True, slots=True)
class Bin:
    lower: float
    upper: float
    count: int
    mean_confidence: float
    hit_rate: float


@dataclass(frozen=True, slots=True)
class Calibration:
    count: int
    hits: int
    bins: tuple[Bin, ...]
    brier: float | None
    expected_calibration_error: float | None


def reliability(
    pairs: Iterable[tuple[float, bool]], *, width: float = DEFAULT_BIN_WIDTH
) -> Calibration:
    """Bin ``(stated confidence, came true)`` pairs and summarise them.

    Bins are ``[lower, upper)`` except the last, which includes 1.0. Empty
    bins are left out: a bucket with no predictions has no hit rate, and
    drawing it at 0 would read as "always wrong".
    """
    if not 0 < width <= 1:
        raise ValueError("width must be in (0, 1]")
    pairs = [(min(1.0, max(0.0, float(c))), bool(hit)) for c, hit in pairs]
    if not pairs:
        return Calibration(0, 0, (), None, None)

    edges = int(round(1 / width))
    grouped: dict[int, list[tuple[float, bool]]] = {}
    for confidence, hit in pairs:
        index = min(int(confidence / width + 1e-9), edges - 1)
        grouped.setdefault(index, []).append((confidence, hit))

    bins = []
    for index in sorted(grouped):
        members = grouped[index]
        bins.append(
            Bin(
                lower=round(index * width, 6),
                upper=round(min(1.0, (index + 1) * width), 6),
                count=len(members),
                mean_confidence=sum(c for c, _ in members) / len(members),
                hit_rate=sum(hit for _, hit in members) / len(members),
            )
        )

    total = len(pairs)
    brier = sum((c - (1.0 if hit else 0.0)) ** 2 for c, hit in pairs) / total
    ece = sum(b.count / total * abs(b.hit_rate - b.mean_confidence) for b in bins)
    return Calibration(
        count=total,
        hits=sum(hit for _, hit in pairs),
        bins=tuple(bins),
        brier=brier,
        expected_calibration_error=ece,
    )


async def _evaluated(db: AsyncSession, user_id: str | None) -> Sequence:
    query = select(models.AthletePrediction).where(
        models.AthletePrediction.status.in_(_EVALUATED)
    )
    if user_id is not None:
        query = query.where(models.AthletePrediction.user_id == user_id)
    return (await db.scalars(query)).all()


async def measured_pairs(
    db: AsyncSession, user_id: str | None = None
) -> list[tuple[float, bool]]:
    """Evaluated predictions that carry the confidence they were made with."""
    return [
        (row.stated_confidence, row.status == "correct")
        for row in await _evaluated(db, user_id)
        if row.stated_confidence is not None
    ]


async def reconstructed_pairs(
    db: AsyncSession, user_id: str | None = None
) -> tuple[list[tuple[float, bool]], int]:
    """An *estimate* for evaluated rows from before ``stated_confidence``.

    Undoes the evaluation's ±step. It is exact only if nobody edited the
    confidence and the step was not clamped; a stored 1.0 after a hit (or 0.0
    after a miss) may have been clamped, so those rows are skipped and counted.
    Returns ``(pairs, skipped)``. Never merge these into :func:`measured_pairs`.
    """
    step = crud.ATHLETE_PREDICTION_CONFIDENCE_STEP
    pairs: list[tuple[float, bool]] = []
    skipped = 0
    for row in await _evaluated(db, user_id):
        if row.stated_confidence is not None:
            continue
        hit = row.status == "correct"
        if (hit and row.confidence >= 1.0) or (not hit and row.confidence <= 0.0):
            skipped += 1
            continue
        pairs.append((round(row.confidence - step if hit else row.confidence + step, 6), hit))
    return pairs, skipped


def render_markdown(title: str, calibration: Calibration) -> str:
    """The report as a table — the reliability diagram in text form."""
    lines = [f"## {title}", ""]
    if not calibration.count:
        lines.append("_No evaluated predictions._")
        return "\n".join(lines)
    lines += [
        f"{calibration.count} evaluated predictions, {calibration.hits} came true.",
        "",
        f"- Brier score: **{calibration.brier:.3f}** (always saying 0.5 scores 0.250)",
        "- Expected calibration error: "
        f"**{calibration.expected_calibration_error:.3f}**",
        "",
        "| stated confidence | predictions | mean stated | came true |",
        "|---|---|---|---|",
    ]
    for b in calibration.bins:
        lines.append(
            f"| {b.lower:.1f}–{b.upper:.1f} | {b.count} | {b.mean_confidence:.2f} "
            f"| {b.hit_rate:.2f} |"
        )
    return "\n".join(lines)
