"""Cost per active user from ``llm_calls`` (ai-trainer-ops#8).

The arithmetic is checked against figures worked out by hand at the coach
model's price ($0.30 / $2.50 per million). The database half checks the window
and that every source the code writes lands in the bucket the issue asks for.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import crud
import models
from auth import hash_password
from scripts import cost_report
from scripts.cost_report import Usage
from tests.conftest import TestSessionLocal

COACH = "gemini-3.5-flash-lite"


def test_a_million_of_each_costs_the_list_price():
    usage = Usage("u", "api:ask-trainer", COACH, 1, 1_000_000, 1_000_000)
    assert cost_report.cost_of(usage) == pytest.approx(2.80)


def test_an_unknown_model_is_not_guessed_at():
    usage = Usage("u", "api:ask-trainer", "gpt-4o", 1, 1_000_000, 0)
    assert cost_report.cost_of(usage) is None

    text = cost_report.render_markdown([usage], 30)
    assert "_No priced, attributed calls in this window._" in text
    assert "- `gpt-4o`: 1,000,000 input, 0 output tokens" in text


def test_median_and_most_expensive_user_are_split_by_bucket():
    usages = [
        # a: $0.25 chat + $0.30 scheduled = $0.55
        Usage("a", "api:ask-trainer", COACH, 3, 0, 100_000),
        Usage("a", "step:insight-generation", COACH, 1, 1_000_000, 0),
        # b: $0.03 ride review
        Usage("b", "job:intervals-sync", COACH, 2, 100_000, 0),
        # c: $0.10 other
        Usage("c", "api:generate-plan", COACH, 1, 0, 40_000),
        # nobody: $0.003
        Usage(None, "unscoped", COACH, 1, 10_000, 0),
    ]

    text = cost_report.render_markdown(usages, 30)

    assert "3 active users. Median **$0.1000**, mean $0.2267, most expensive **$0.5500**." in text
    assert "| scheduled analysis | $0.1000 | $0.3000 |" in text
    assert "| per-ride review | $0.0100 | $0.0000 |" in text
    assert "| chat | $0.0833 | $0.2500 |" in text
    assert "| other | $0.0333 | $0.0000 |" in text
    assert "| **total** | $0.2267 | $0.5500 |" in text
    assert "Calls billed to no user (`user_id` empty): $0.0030." in text
    # The per-source table is ordered by cost and keeps the unscoped calls.
    assert text.index("`step:insight-generation`") < text.index("`api:ask-trainer`")
    assert "| `unscoped` | other | 1 | 10,000 | 0 | $0.0030 |" in text


def test_every_source_the_code_writes_has_a_known_bucket_or_is_other():
    """A renamed source would silently fall into "other". Read the literals
    the code actually passes and require each mapped one to still exist."""
    root = Path(__file__).resolve().parents[1]
    written = set()
    for path in [*root.glob("routers/*.py"), *root.glob("services/*.py")]:
        written |= set(re.findall(r'"((?:api|job|bg|step):[a-z0-9-]+)"', path.read_text()))
    stale = set(cost_report._BUCKET_OF_SOURCE) - written
    assert not stale, f"mapped sources no call site writes any more: {sorted(stale)}"


async def _user(email: str) -> str:
    async with TestSessionLocal() as db:
        user = await crud.create_user(
            db, email=email, name="T", hashed_password=hash_password("pw")
        )
        await db.commit()
        return user.id


async def _call(user_id: str, source: str, created_at: datetime, output_tokens: int) -> None:
    async with TestSessionLocal() as db:
        db.add(
            models.LlmCall(
                user_id=user_id,
                created_at=created_at,
                task="t",
                provider="gemini",
                model=COACH,
                source=source,
                input_tokens=0,
                output_tokens=output_tokens,
                prompt_sha="0" * 12,
            )
        )
        await db.commit()


async def test_the_report_reads_only_the_window(monkeypatch):
    now = datetime(2026, 10, 10, tzinfo=timezone.utc)
    user_id = await _user("cost-report@example.com")
    await _call(user_id, "api:ask-trainer", now - timedelta(days=1), 400_000)  # $1.00
    await _call(user_id, "api:ask-trainer", now - timedelta(days=1), 400_000)  # $1.00
    await _call(user_id, "api:ask-trainer", now - timedelta(days=31), 4_000_000)  # outside
    monkeypatch.setattr(cost_report, "async_session_maker", TestSessionLocal)

    text = await cost_report.build(30, now=now)

    assert "most expensive **$2.0000**" in text
    assert "| `api:ask-trainer` | chat | 2 | 0 | 800,000 | $2.0000 |" in text


def test_the_script_parses_its_flag(monkeypatch, capsys):
    async def fake_build(days: int) -> str:
        return f"built days={days}"

    monkeypatch.setattr(cost_report, "build", fake_build)
    assert cost_report.main(["--days", "7"]) == 0
    assert "built days=7" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cost_report.main(["--days", "0"])
