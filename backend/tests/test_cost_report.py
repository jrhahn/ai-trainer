"""Cost per active user from ``llm_calls`` (ai-trainer-ops#8).

The arithmetic is checked against figures worked out by hand at the coach
model's price ($0.30 input, $0.03 cached input, $2.50 output per million). The
database half checks the window and that every source the code writes lands in
the bucket the issue asks for.
"""

from __future__ import annotations

import ast
import json
import operator
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import crud
import models
from domain.passwords import hash_password
from scripts import cost_report
from scripts.cost_report import Usage
from tests.conftest import TestSessionLocal

COACH = "gemini-3.5-flash-lite"


def _source_row(text: str, source: str) -> list[str]:
    """The cells of the by-source table's row for ``source``, after its name."""
    (line,) = [ln for ln in text.splitlines() if ln.startswith(f"| `{source}` |")]
    return [cell.strip() for cell in line.strip("|").split("|")][1:]


def test_a_million_of_each_costs_the_list_price():
    usage = Usage("u", "api:ask-trainer", COACH, 1, 1_000_000, 1_000_000)
    assert cost_report.cost_of(usage) == pytest.approx(2.80)


def test_a_cached_token_bills_at_the_cached_rate():
    """``cached_tokens`` is part of ``input_tokens``, so it leaves the input
    term instead of adding to it (ai-trainer-ops#257)."""
    usage = Usage("u", "api:ask-trainer", COACH, 1, 1_000_000, 0, 600_000)
    # 400,000 uncached × $0.30/M + 600,000 cached × $0.03/M
    assert cost_report.cost_of(usage) == pytest.approx(0.138)


def _evaluate(expression: str) -> float:
    """Arithmetic only: a number, + - * / and brackets, nothing else."""
    operations = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
    }

    def value(node: ast.expr) -> float:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in operations:
            return operations[type(node.op)](value(node.left), value(node.right))
        raise ValueError(f"not arithmetic: {ast.dump(node)}")

    return value(ast.parse(expression, mode="eval").body)


def test_the_grafana_spend_panel_prices_like_the_report():
    """The panel and the report are two copies of one price list; a cache hit
    must lower both (ai-trainer-ops#257). The panel's query is evaluated with
    each token series replaced by a known count."""
    dashboard = Path(__file__).resolve().parents[2] / (
        "monitoring/grafana/dashboards/ai-trainer.json"
    )
    panels = json.loads(dashboard.read_text())["panels"]
    (panel,) = [p for p in panels if p.get("title") == "Estimated spend, last 24h"]
    tokens = {"input": 3_000_000, "cached": 2_000_000, "output": 500_000}

    expression = re.sub(
        r'sum\(increase\(llm_tokens_total\{kind="(\w+)"\}\[24h\]\)\)',
        lambda match: str(tokens[match.group(1)]),
        panel["targets"][0]["expr"],
    )

    usage = Usage("u", "api:ask-trainer", COACH, 1, 3_000_000, 500_000, 2_000_000)
    assert _evaluate(expression) == pytest.approx(cost_report.cost_of(usage))


def test_an_unknown_model_is_not_guessed_at():
    usage = Usage("u", "api:ask-trainer", "gpt-4o", 1, 1_000_000, 0)
    assert cost_report.cost_of(usage) is None

    text = cost_report.render_markdown([usage], 30)
    assert "- `gpt-4o`: 1,000,000 input, 0 output tokens" in text


def test_a_user_with_only_unpriced_calls_is_still_active():
    usages = [
        Usage("byok", "api:ask-trainer", "gpt-4o", 1, 1_000_000, 0),
        Usage("coach", "api:ask-trainer", COACH, 1, 0, 400_000),  # $1.00
    ]

    text = cost_report.render_markdown(usages, 30)

    assert "2 active users. Median **$0.5000**, mean $0.5000" in text
    assert "1 of them made calls on an unpriced model" in text


def test_an_empty_window_says_so():
    assert "_No attributed calls in this window._" in cost_report.render_markdown(
        [], 30
    )


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

    for figure in (
        "3 active users",
        "Median **$0.1000**",
        "mean $0.2267",
        "most expensive **$0.5500**",
    ):
        assert figure in text
    assert "| scheduled analysis | $0.1000 | $0.3000 |" in text
    assert "| per-ride review | $0.0100 | $0.0000 |" in text
    assert "| chat | $0.0833 | $0.2500 |" in text
    assert "| other | $0.0333 | $0.0000 |" in text
    assert "| **total** | $0.2267 | $0.5500 |" in text
    assert "Calls billed to no user (`user_id` empty): $0.0030." in text
    # The per-source table is ordered by cost and keeps the unscoped calls.
    assert text.index("`step:insight-generation`") < text.index("`api:ask-trainer`")
    assert _source_row(text, "unscoped") == [
        "other",
        "1",
        "10,000",
        "0",
        "0",
        "$0.0030",
    ]


def test_every_source_the_code_writes_has_a_known_bucket_or_is_other():
    """A renamed source would silently fall into "other". Read the literals
    the code actually passes and require each mapped one to still exist."""
    root = Path(__file__).resolve().parents[1]
    written = set()
    for path in [*root.glob("routers/**/*.py"), *root.glob("services/*.py")]:
        written |= set(
            re.findall(r'"((?:api|job|bg|step):[a-z0-9-]+)"', path.read_text())
        )
    stale = set(cost_report._BUCKET_OF_SOURCE) - written
    assert not stale, f"mapped sources no call site writes any more: {sorted(stale)}"


async def _user(email: str) -> str:
    async with TestSessionLocal() as db:
        user = await crud.create_user(
            db, email=email, name="T", hashed_password=hash_password("pw")
        )
        await db.commit()
        return user.id


async def _call(
    user_id: str,
    source: str,
    created_at: datetime,
    output_tokens: int,
    *,
    input_tokens: int = 0,
    cached_tokens: int = 0,
) -> None:
    async with TestSessionLocal() as db:
        db.add(
            models.LlmCall(
                user_id=user_id,
                created_at=created_at,
                task="t",
                provider="gemini",
                model=COACH,
                source=source,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_tokens=cached_tokens,
                prompt_sha="0" * 12,
            )
        )
        await db.commit()


async def test_the_report_reads_only_the_window(monkeypatch):
    now = datetime(2026, 10, 10, tzinfo=timezone.utc)
    user_id = await _user("cost-report@example.com")
    await _call(user_id, "api:ask-trainer", now - timedelta(days=1), 400_000)  # $1.00
    await _call(user_id, "api:ask-trainer", now - timedelta(days=1), 400_000)  # $1.00
    await _call(
        user_id, "api:ask-trainer", now - timedelta(days=31), 4_000_000
    )  # outside
    monkeypatch.setattr(cost_report, "async_session_maker", TestSessionLocal)

    text = await cost_report.build(30, now=now)

    assert "most expensive **$2.0000**" in text
    assert _source_row(text, "api:ask-trainer") == [
        "chat",
        "2",
        "0",
        "0",
        "800,000",
        "$2.0000",
    ]


async def test_the_report_prices_the_cached_share_of_the_rows(monkeypatch):
    """``build`` reads ``cached_tokens`` off the rows, so a cache hit lowers the
    report's figure as it lowers the bill (ai-trainer-ops#257)."""
    now = datetime(2026, 10, 27, tzinfo=timezone.utc)
    user_id = await _user("cost-report-cached@example.com")
    for cached in (0, 600_000):
        await _call(
            user_id,
            "api:ask-trainer",
            now - timedelta(hours=1),
            0,
            input_tokens=1_000_000,
            cached_tokens=cached,
        )
    monkeypatch.setattr(cost_report, "async_session_maker", TestSessionLocal)

    text = await cost_report.build(30, now=now)

    # $0.30 for the uncached million, then 400,000 × $0.30/M + 600,000 × $0.03/M.
    assert _source_row(text, "api:ask-trainer") == [
        "chat",
        "2",
        "2,000,000",
        "600,000",
        "0",
        "$0.4380",
    ]


def test_the_script_parses_its_flag(monkeypatch, capsys):
    async def fake_build(days: int) -> str:
        return f"built days={days}"

    monkeypatch.setattr(cost_report, "build", fake_build)
    assert cost_report.main(["--days", "7"]) == 0
    assert "built days=7" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cost_report.main(["--days", "0"])
