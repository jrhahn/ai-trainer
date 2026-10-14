"""Print how many "this is wrong" flags came in per week (ai-trainer-ops#94).

Run against a deployment's database, from ``backend/``:

    uv run python -m scripts.wrong_flags_report [--weeks 6]

Counts only. What was flagged and the athletes' notes stay on the server and
are read on the admin page.
"""

from __future__ import annotations

import argparse
import asyncio

import crud
from database import async_session_maker


def render(counts: list[tuple[object, int]]) -> str:
    lines = [
        "#### \"This is wrong\" flags",
        "",
        "| Week from | Flags |",
        "|---|---:|",
        *(f"| {start} | {count} |" for start, count in counts),
    ]
    return "\n".join(lines)


async def build(weeks: int) -> str:
    async with async_session_maker() as session:
        return render(await crud.wrong_flag_weekly_counts(session, weeks))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--weeks", type=int, default=6, help="rolling weeks to count")
    args = parser.parse_args(argv)
    print(asyncio.run(build(max(1, args.weeks))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
