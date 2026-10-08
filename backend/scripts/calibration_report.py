"""Print the coach's calibration as Markdown (ai-trainer-ops#27).

Run against a deployment's database, from ``backend/``:

    uv run python -m scripts.calibration_report [--reconstructed]

The measured table uses only predictions that recorded the confidence they were
made with. ``--reconstructed`` adds a second, separately titled table for older
rows, estimated by undoing the evaluation's nudge — useful for a first look,
never to be read as the measurement. See ``services/calibration``.

Prints aggregates only: counts, rates and scores, nothing about any athlete.
"""

from __future__ import annotations

import argparse
import asyncio

from database import async_session_maker
from services import calibration


async def build(reconstructed: bool) -> str:
    async with async_session_maker() as session:
        sections = [
            calibration.render_markdown(
                "Measured (stated confidence)",
                calibration.reliability(await calibration.measured_pairs(session)),
            )
        ]
        if reconstructed:
            pairs, skipped = await calibration.reconstructed_pairs(session)
            sections.append(
                calibration.render_markdown(
                    "Reconstructed (estimate, older rows)",
                    calibration.reliability(pairs),
                )
                + f"\n\n{skipped} rows skipped: the nudge may have been clamped. "
                "Rows an athlete edited cannot be told apart and are included, "
                "which is why this is an estimate."
            )
    return "\n\n".join(sections)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--reconstructed",
        action="store_true",
        help="also estimate older rows by undoing the evaluation nudge",
    )
    args = parser.parse_args(argv)
    print(asyncio.run(build(args.reconstructed)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
