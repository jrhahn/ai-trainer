"""Backtest the performance model against its own later estimate (ai-trainer-ops#27).

Run against a deployment's database, from ``backend/``:

    uv run python -m scripts.backtest_report [--weeks 6]

For every athlete with ride metrics: hide the last ``--weeks`` weeks, infer the
performance attributes (FTP, MAP, critical speed, threshold pace, …) from what
was known then, and compare each numeric estimate with the one the model makes
today from all the data. Where the earlier estimate stated a range
(``estimate_low``/``estimate_high``), it also counts how often today's value
fell inside it: a range that is right 30% of the time is not a range.

**The reference is the model's later estimate, not a lab test.** Nothing in the
database is ground truth for FTP; a later estimate has more evidence behind it,
which is what makes the comparison worth reading, but a model that is
consistently wrong in the same direction agrees with itself perfectly. Read
this as "how much does the estimate move once more data arrives", which is the
part of calibration the data can answer.

Prints aggregates only: counts, percentages and coverage, nothing about any
athlete.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import select

import crud
import models
from database import async_session_maker
from services import athlete_model_inference as ami

# Enough history to fill the earlier window as well as today's.
_HISTORY_LIMIT = 2000


@dataclass(frozen=True, slots=True)
class Point:
    attribute: str
    earlier: float
    later: float
    low: float | None
    high: float | None


def _estimate(attr: dict | None) -> float | None:
    value = (attr or {}).get("estimate")
    return float(value) if isinstance(value, (int, float)) and value else None


def compare(earlier: dict[str, dict], later: dict[str, dict]) -> list[Point]:
    """One point per attribute that has a numeric estimate at both times."""
    points = []
    for name in sorted(earlier.keys() & later.keys()):
        before, after = _estimate(earlier[name]), _estimate(later[name])
        if before is None or after is None:
            continue
        points.append(
            Point(
                name,
                before,
                after,
                earlier[name].get("estimate_low"),
                earlier[name].get("estimate_high"),
            )
        )
    return points


def render_markdown(points: Iterable[Point], weeks: int, athletes: int) -> str:
    by_attribute: dict[str, list[Point]] = {}
    for point in points:
        by_attribute.setdefault(point.attribute, []).append(point)

    lines = [
        f"## Estimates {weeks} weeks ago against today's",
        "",
        f"{athletes} athletes with ride metrics. The reference is the model's own "
        "later estimate, not a measurement.",
        "",
    ]
    if not by_attribute:
        lines.append("_No attribute had an estimate at both times._")
        return "\n".join(lines)
    lines += [
        "| attribute | athletes | median abs. change | mean signed change "
        "| later value inside earlier range |",
        "|---|---|---|---|---|",
    ]
    for name, group in sorted(by_attribute.items()):
        changes = [(p.earlier - p.later) / p.later * 100 for p in group]
        ranged = [p for p in group if p.low is not None and p.high is not None]
        inside = sum(p.low <= p.later <= p.high for p in ranged)
        coverage = f"{inside}/{len(ranged)}" if ranged else "no range stated"
        lines.append(
            f"| {name} | {len(group)} | {statistics.median(abs(c) for c in changes):.1f}% "
            f"| {statistics.fmean(changes):+.1f}% | {coverage} |"
        )
    lines += [
        "",
        "Signed change is earlier minus later, as a share of later: negative means "
        "the earlier estimate was lower than what the model says now.",
    ]
    return "\n".join(lines)


def _known_by(metrics: list, day: str) -> list:
    """The newest-first window the model would have read on ``day``."""
    return [m for m in metrics if m.activity_date <= day][: ami.PERF_WINDOW_RIDES]


async def build(weeks: int, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    then = now - timedelta(weeks=weeks)
    points: list[Point] = []
    async with async_session_maker() as session:
        user_ids = (
            await session.scalars(select(models.RideMetric.user_id).distinct())
        ).all()
        for user_id in user_ids:
            metrics = await crud.get_ride_metrics_history(
                session, user_id, limit=_HISTORY_LIMIT
            )
            earlier = ami.infer_performance_attributes(
                _known_by(metrics, then.date().isoformat()), now=then
            )
            later = ami.infer_performance_attributes(
                _known_by(metrics, now.date().isoformat()), now=now
            )
            points += compare(earlier, later)
    return render_markdown(points, weeks, len(user_ids))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--weeks", type=int, default=6, help="how far back to hide data (default 6)"
    )
    args = parser.parse_args(argv)
    if args.weeks < 1:
        parser.error("--weeks must be at least 1")
    print(asyncio.run(build(args.weeks)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
