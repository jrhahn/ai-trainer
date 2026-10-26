"""Print where a tester wave stands against the gates for the next one (ai-trainer-ops#96).

Run against a deployment's database, from ``backend/``:

    uv run python -m scripts.wave_gates_report --since 2026-10-26 [--exclude-email ...]

A tester is every account created on or after ``--since``, less the excluded
addresses (the owner's own). The gates, and what this measures for each:

- *a first plan from their own data in under 15 minutes, without help*: the
  minutes from sign-up to the first plan, and whether a ride had arrived by then.
  "Without help" is the owner's to judge.
- *no embarrassing plan*: "this is wrong" on a planned day, and coach replies that
  reached the athlete with the note that a plan change breaks a planning rule.
  Both are signs to read, not the verdict.
- *no reply with an invented number*: coach replies that reached the athlete with
  the note that a number could not be traced to their data. A rewrite that fixed
  the number leaves no note and is not counted.
- *every "this is wrong" answered or turned into an issue*: the flags still open
  on the admin page.

Counts and minutes only. No address, name, flag text or reply leaves the server.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

import models
from database import async_session_maker
from services import reply_notes
from services.ai_service import NUMBER_NOTE_MARKERS, RULE_NOTE_MARKERS

FIRST_PLAN_MINUTES = 15


@dataclass
class WaveFigures:
    testers: int = 0
    # Minutes from sign-up to a first plan written after a ride had arrived.
    own_data_minutes: list[int] = field(default_factory=list)
    # A first plan written before any ride arrived.
    plan_before_rides: int = 0
    no_plan: int = 0
    replies: int = 0
    number_notes: int = 0
    rule_notes: int = 0
    day_flags: int = 0
    reply_flags: int = 0
    open_flags: int = 0

    @property
    def fast(self) -> int:
        return sum(1 for m in self.own_data_minutes if m < FIRST_PLAN_MINUTES)


def _aware(when: datetime) -> datetime:
    # SQLite hands back naive datetimes; the columns are UTC either way.
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _contains_any(column, markers: tuple[str, ...]):
    return or_(*(column.contains(marker) for marker in markers))


def _noted(kinds: tuple[str, ...], legacy_markers: tuple[str, ...]):
    """A reply carrying one of ``kinds`` among its notes (ai-trainer-ops#262), or,
    written before the notes left the text, one of ``legacy_markers`` in it."""
    notes = cast(models.ChatMessage.notes_json, String)
    return or_(
        # autoescape: the "_" in a kind is a LIKE wildcard otherwise (review of #1136).
        *(notes.contains(f'"{kind}"', autoescape=True) for kind in kinds),
        _contains_any(models.ChatMessage.content, legacy_markers),
    )


async def gather(
    session: AsyncSession, since: date, exclude_emails: list[str] | None = None
) -> WaveFigures:
    start = datetime.combine(since, time.min, tzinfo=timezone.utc)
    excluded = {
        email.strip().lower() for email in exclude_emails or [] if email.strip()
    }
    users = [
        user
        for user in (
            await session.scalars(
                select(models.User).where(models.User.created_at >= start)
            )
        ).all()
        if user.email.lower() not in excluded
    ]
    figures = WaveFigures(testers=len(users))
    if not users:
        return figures
    ids = [user.id for user in users]

    plans = {
        row.user_id: _aware(row.created_at)
        for row in (
            await session.scalars(
                select(models.TrainingPlan).where(models.TrainingPlan.user_id.in_(ids))
            )
        ).all()
    }
    first_rides = {
        user_id: _aware(first)
        for user_id, first in (
            await session.execute(
                select(
                    models.RideMetric.user_id, func.min(models.RideMetric.created_at)
                )
                .where(models.RideMetric.user_id.in_(ids))
                .group_by(models.RideMetric.user_id)
            )
        ).all()
    }
    for user in users:
        plan_at = plans.get(user.id)
        ride_at = first_rides.get(user.id)
        if plan_at is None:
            figures.no_plan += 1
        elif ride_at is None or ride_at > plan_at:
            figures.plan_before_rides += 1
        else:
            seconds = (plan_at - _aware(user.created_at)).total_seconds()
            figures.own_data_minutes.append(max(0, int(seconds // 60)))
    figures.own_data_minutes.sort()

    assistant = (
        select(func.count())
        .select_from(models.ChatMessage)
        .where(
            models.ChatMessage.user_id.in_(ids),
            models.ChatMessage.role == "assistant",
            models.ChatMessage.created_at >= start,
        )
    )
    figures.replies = await session.scalar(assistant) or 0
    figures.number_notes = (
        await session.scalar(
            assistant.where(
                _noted((reply_notes.UNTRACED_NUMBERS,), NUMBER_NOTE_MARKERS)
            )
        )
        or 0
    )
    figures.rule_notes = (
        await session.scalar(
            assistant.where(
                _noted(
                    (reply_notes.PLAN_CHANGE_PRIORS, reply_notes.STANDING_PRIORS),
                    RULE_NOTE_MARKERS,
                )
            )
        )
        or 0
    )

    flags = (
        await session.scalars(
            select(models.WrongFlag).where(
                models.WrongFlag.user_id.in_(ids), models.WrongFlag.created_at >= start
            )
        )
    ).all()
    figures.day_flags = sum(1 for flag in flags if flag.target == "day")
    figures.reply_flags = sum(1 for flag in flags if flag.target == "reply")
    figures.open_flags = sum(1 for flag in flags if flag.handled_at is None)
    return figures


def _mark(ok: bool) -> str:
    return "met" if ok else "**not met**"


def render(figures: WaveFigures, since: date) -> str:
    head = [f"#### Wave gates (ai-trainer-ops#96), testers signed up since {since}", ""]
    if not figures.testers:
        return "\n".join([*head, "No tester account yet."])

    minutes = ", ".join(str(m) for m in figures.own_data_minutes) or "none"
    flags = figures.day_flags + figures.reply_flags
    first_plan_ok = figures.fast == figures.testers
    plan_ok = figures.day_flags == 0 and figures.rule_notes == 0
    number_ok = figures.number_notes == 0
    flags_ok = figures.open_flags == 0
    lines = [
        *head,
        f"Testers: {figures.testers}.",
        "",
        "| Gate | Measured | |",
        "|---|---|---|",
        (
            f"| First plan from own data in under {FIRST_PLAN_MINUTES} min "
            f"| {figures.fast} of {figures.testers}; minutes: {minutes}; "
            f"plan before any ride: {figures.plan_before_rides}; no plan: {figures.no_plan} "
            f"| {_mark(first_plan_ok)} |"
        ),
        (
            f'| No embarrassing plan | "this is wrong" on a day: {figures.day_flags}; '
            f"replies noting a broken planning rule: {figures.rule_notes} "
            f"| {'no sign' if plan_ok else '**read them**'} |"
        ),
        (
            f"| No reply with an invented number | {figures.number_notes} of {figures.replies} "
            f"replies | {_mark(number_ok)} |"
        ),
        (
            f'| Every "this is wrong" answered or an issue | {figures.open_flags} of {flags} '
            f"still open | {_mark(flags_ok)} |"
        ),
        "",
        '"Without help" and whether a flagged plan was embarrassing are the owner\'s to '
        "judge. A first plan written before any ride arrived does not count as one from "
        "the tester's own data, even when a later rebuild used them.",
    ]
    return "\n".join(lines)


async def build(since: date, exclude_emails: list[str]) -> str:
    async with async_session_maker() as session:
        return render(await gather(session, since, exclude_emails), since)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--since",
        type=date.fromisoformat,
        required=True,
        help="the wave's first day, ISO",
    )
    parser.add_argument(
        "--exclude-email",
        action="append",
        default=[],
        help="an account that is not a tester, such as the owner's; repeatable",
    )
    args = parser.parse_args(argv)
    print(asyncio.run(build(args.since, args.exclude_email)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
