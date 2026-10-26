"""No test pins a sentence of 60 characters or more (ai-trainer-ops#259).

A wording change in a prompt used to break a dozen tests, each holding one of
its sentences. The wording is now in the snapshots
(``test_prompt_snapshots.py``). A test asserts the data the code computed, the
code's own constant, or a short key phrase for the rule it guards. This test
fails on a new ``assert "<60 or more characters>" in ...``. It also catches a
literal joined over several lines, which ``rg 'assert "[^"]{60,}" in'`` misses.
"""

from __future__ import annotations

import ast
from pathlib import Path

LIMIT = 60
TESTS = Path(__file__).parent


def _long_pins(source: str) -> list[int]:
    """The lines of each ``assert "<long literal>" in ...`` in ``source``."""
    lines = []
    for statement in ast.walk(ast.parse(source)):
        if not isinstance(statement, ast.Assert):
            continue
        for node in ast.walk(statement.test):
            if (
                isinstance(node, ast.Compare)
                and isinstance(node.left, ast.Constant)
                and isinstance(node.left.value, str)
                and len(node.left.value) >= LIMIT
                and any(isinstance(op, ast.In) for op in node.ops)
            ):
                lines.append(node.lineno)
    return lines


def test_no_test_pins_a_long_sentence():
    pins = [
        f"{path.relative_to(TESTS)}:{line}"
        for path in sorted(TESTS.rglob("*.py"))
        for line in _long_pins(path.read_text(encoding="utf-8"))
    ]
    assert pins == [], (
        f"these asserts pin a literal of {LIMIT} or more characters with `in`: assert the "
        "data instead, or a short key phrase, and let the prompt snapshot hold the wording"
    )


def test_the_guard_sees_a_literal_joined_over_lines():
    joined = (
        "def test():\n    assert (\n        "
        + '"'
        + "a" * 30
        + '"\n        "'
        + "b" * 30
        + '"\n    ) in text\n'
    )
    short = 'def test():\n    assert "' + "a" * (LIMIT - 1) + '" in text\n'
    absent = 'def test():\n    assert "' + "a" * LIMIT + '" not in text\n'

    assert _long_pins(joined) == [2]  # the assert's line
    assert _long_pins(short) == _long_pins(absent) == []
