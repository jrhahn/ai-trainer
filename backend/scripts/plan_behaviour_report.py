"""How the coach's plans behave over weeks, not days (ai-trainer-ops#66).

Run against a deployment's database, from ``backend/``:

    uv run python -m scripts.plan_behaviour_report [--weeks 12] [--user-email a@b.c]

The owner's impression was that the coach recommends roughly the same thing
every time, with no periodization. Nothing measured it. This reads what was
actually planned (``plan_day_history``, the last applied version of each day)
and what was actually ridden (``ride_metrics``) and states, per athlete:

- **Weekly volume**, planned and ridden, and its coefficient of variation. A
  plan whose weekly volume never varies has no rhythm.
- **Recovery weeks**: weeks at most 75% of the three before them. Twelve weeks
  with none is the "no periodization" finding in one number.
- **Week-to-week similarity** of the session mix (minutes per workout type,
  cosine). Close to 1.0 week after week is "always the same".
- **Hard share per 4-week block** (intervals and race minutes over all planned
  minutes) and how far it moves between blocks.

Without ``--user-email`` it prints aggregates only, and hides any figure fewer
than ``MIN_ATHLETES`` athletes contribute to, like ``backtest_report``. With it,
it prints that one athlete's own figures, for the athlete to read.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import statistics
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

import models
from database import async_session_maker

MIN_ATHLETES = 3
LIGHT_WEEK_RATIO = 0.75
BLOCK_WEEKS = 4
HARD_TYPES = frozenset({"intervals", "race"})
# A week the plan has nothing in is not a planned week, it is missing history.
MIN_PLANNED_DAYS_PER_WEEK = 3


@dataclass(frozen=True)
class AthleteBehaviour:
    weeks: int
    planned_minutes: tuple[float, ...]
    ridden_minutes: tuple[float, ...]
    planned_recovery_weeks: int
    ridden_recovery_weeks: int
    planned_cv: float | None
    mean_similarity: float | None
    hard_share_by_block: tuple[float, ...]

    @property
    def hard_share_spread(self) -> float | None:
        if len(self.hard_share_by_block) < 2:
            return None
        return max(self.hard_share_by_block) - min(self.hard_share_by_block)


def _as_date(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def final_plan(history: Iterable[object]) -> dict[tuple[date, int], dict]:
    """The last applied version of every planned day, keyed by (date, slot)."""
    latest: dict[tuple[date, int], tuple[datetime | None, dict | None]] = {}
    for row in history:
        if not getattr(row, "applied", True):
            continue
        day = _as_date(getattr(row, "date", None))
        if day is None:
            continue
        key = (day, int(getattr(row, "slot", 0) or 0))
        recorded = getattr(row, "recorded_at", None)
        previous = latest.get(key)
        if (
            previous is None
            or recorded is None
            or previous[0] is None
            or recorded >= previous[0]
        ):
            latest[key] = (recorded, getattr(row, "new_day", None))
    return {key: day for key, (_, day) in latest.items() if isinstance(day, dict)}


def _minutes(day: dict) -> float:
    value = day.get("durationMinutes")
    return float(value) if isinstance(value, (int, float)) and value > 0 else 0.0


def _week_starts(today: date, weeks: int) -> list[date]:
    """Start dates of rolling 7-day weeks ending yesterday, oldest first."""
    end = today - timedelta(days=1)
    return [end - timedelta(days=7 * back + 6) for back in range(weeks - 1, -1, -1)]


def _light_weeks(series: list[float]) -> int:
    count = 0
    for index in range(3, len(series)):
        reference = sum(series[index - 3 : index]) / 3
        if reference > 0 and series[index] <= LIGHT_WEEK_RATIO * reference:
            count += 1
    return count


def _cosine(a: dict[str, float], b: dict[str, float]) -> float | None:
    keys = a.keys() | b.keys()
    dot = sum(a.get(k, 0) * b.get(k, 0) for k in keys)
    norm = math.sqrt(sum(v * v for v in a.values())) * math.sqrt(
        sum(v * v for v in b.values())
    )
    return dot / norm if norm else None


def behaviour(
    history: Iterable[object], rides: Iterable[object], today: date, weeks: int
) -> AthleteBehaviour | None:
    """One athlete's plan behaviour, or ``None`` with too little planned history."""
    plan = final_plan(history)
    starts = _week_starts(today, weeks)
    planned, mixes, planned_days, hard, total = [], [], [], [], []
    for start in starts:
        days = [d for (when, _), d in plan.items() if start <= when < start + timedelta(days=7)]
        mix: dict[str, float] = {}
        for day in days:
            kind = str(day.get("workoutType") or "unknown")
            mix[kind] = mix.get(kind, 0.0) + _minutes(day)
        planned.append(sum(mix.values()))
        mixes.append(mix)
        planned_days.append(len({when for (when, _) in plan if start <= when < start + timedelta(days=7)}))
        hard.append(sum(v for k, v in mix.items() if k in HARD_TYPES))
        total.append(sum(mix.values()))

    covered = [i for i, n in enumerate(planned_days) if n >= MIN_PLANNED_DAYS_PER_WEEK]
    if len(covered) < 4:
        return None
    first = covered[0]
    planned = planned[first:]
    mixes = mixes[first:]
    hard, total = hard[first:], total[first:]

    ridden_by_day: dict[date, float] = {}
    for ride in rides:
        when = _as_date(getattr(ride, "activity_date", None))
        if when is not None:
            ridden_by_day[when] = ridden_by_day.get(when, 0.0) + (
                getattr(ride, "duration_seconds", None) or 0
            ) / 60
    ridden = [
        sum(ridden_by_day.get(start + timedelta(days=d), 0.0) for d in range(7))
        for start in starts[first:]
    ]

    similarities = [
        s for s in (_cosine(a, b) for a, b in zip(mixes, mixes[1:])) if s is not None
    ]
    mean = statistics.fmean(planned)
    blocks = []
    for index in range(0, len(planned) - BLOCK_WEEKS + 1, BLOCK_WEEKS):
        block_total = sum(total[index : index + BLOCK_WEEKS])
        if block_total:
            blocks.append(sum(hard[index : index + BLOCK_WEEKS]) / block_total)
    return AthleteBehaviour(
        weeks=len(planned),
        planned_minutes=tuple(round(m) for m in planned),
        ridden_minutes=tuple(round(m) for m in ridden),
        planned_recovery_weeks=_light_weeks(planned),
        ridden_recovery_weeks=_light_weeks(ridden),
        planned_cv=(statistics.pstdev(planned) / mean) if mean else None,
        mean_similarity=statistics.fmean(similarities) if similarities else None,
        hard_share_by_block=tuple(round(b, 3) for b in blocks),
    )


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def render_athlete(result: AthleteBehaviour | None) -> str:
    lines = ["## Plan behaviour, one athlete", ""]
    if result is None:
        lines.append("_Fewer than 4 weeks with a planned week on record._")
        return "\n".join(lines)
    lines += [
        f"Weeks measured: {result.weeks} (rolling weeks ending yesterday, oldest first).",
        "",
        f"- Planned minutes per week: {', '.join(map(str, result.planned_minutes))}",
        f"- Ridden minutes per week: {', '.join(map(str, result.ridden_minutes))}",
        f"- Recovery weeks (at most {LIGHT_WEEK_RATIO:.0%} of the 3 before): "
        f"planned {result.planned_recovery_weeks}, ridden {result.ridden_recovery_weeks}",
        f"- Variation of planned weekly volume (CV): {_pct(result.planned_cv)}",
        f"- Mean week-to-week similarity of the session mix: "
        + ("n/a" if result.mean_similarity is None else f"{result.mean_similarity:.2f}"),
        f"- Hard share per {BLOCK_WEEKS}-week block: "
        + (", ".join(_pct(b) for b in result.hard_share_by_block) or "n/a")
        + f" (spread {_pct(result.hard_share_spread)})",
    ]
    return "\n".join(lines)


def render_aggregate(results: list[AthleteBehaviour], athletes: int) -> str:
    lines = [
        "## Plan behaviour over weeks",
        "",
        f"{athletes} athletes with plan history; {len(results)} with at least 4 "
        "planned weeks.",
        "",
    ]
    if len(results) < MIN_ATHLETES:
        lines.append(
            f"_Not shown: fewer than {MIN_ATHLETES} athletes. "
            "Run with `--user-email` for one athlete's own figures._"
        )
        return "\n".join(lines)

    def median(values: list[float | None]) -> float | None:
        values = [v for v in values if v is not None]
        return statistics.median(values) if values else None

    without_recovery = sum(r.planned_recovery_weeks == 0 for r in results)
    lines += [
        "| measure | median across athletes |",
        "|---|---|",
        f"| planned recovery weeks | {median([r.planned_recovery_weeks for r in results])} |",
        f"| ridden recovery weeks | {median([r.ridden_recovery_weeks for r in results])} |",
        f"| CV of planned weekly volume | {_pct(median([r.planned_cv for r in results]))} |",
        "| week-to-week similarity of the session mix | "
        + (lambda m: "n/a" if m is None else f"{m:.2f}")(
            median([r.mean_similarity for r in results])
        )
        + " |",
        f"| spread of hard share between blocks | {_pct(median([r.hard_share_spread for r in results]))} |",
        "",
        f"Athletes with no planned recovery week at all: {without_recovery} of {len(results)}.",
    ]
    return "\n".join(lines)


async def build(weeks: int, user_email: str | None = None, today: date | None = None) -> str:
    today = today or datetime.now(timezone.utc).date()
    since = (today - timedelta(days=7 * weeks + 7)).isoformat()
    async with async_session_maker() as session:
        query = select(models.User.id)
        if user_email:
            query = query.where(models.User.email == user_email)
        user_ids = (await session.scalars(query)).all()
        results = []
        with_history = 0
        for user_id in user_ids:
            history = (
                await session.scalars(
                    select(models.PlanDayHistory).where(
                        models.PlanDayHistory.user_id == user_id,
                        models.PlanDayHistory.date >= since,
                    )
                )
            ).all()
            if not history:
                continue
            with_history += 1
            rides = (
                await session.scalars(
                    select(models.RideMetric).where(
                        models.RideMetric.user_id == user_id,
                        models.RideMetric.activity_date >= since,
                    )
                )
            ).all()
            result = behaviour(history, rides, today, weeks)
            if user_email:
                return render_athlete(result)
            if result is not None:
                results.append(result)
    if user_email:
        return render_athlete(None)
    return render_aggregate(results, with_history)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--weeks", type=int, default=12, help="weeks to measure (default 12)")
    parser.add_argument("--user-email", default=None, help="one athlete's own figures")
    args = parser.parse_args(argv)
    if args.weeks < 4:
        parser.error("--weeks must be at least 4")
    print(asyncio.run(build(args.weeks, args.user_email)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
