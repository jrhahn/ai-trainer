"""One key normaliser, and one answer for an edit or a delete (ai-trainer-ops#249).

The memory facts and the coach's hypotheses, open questions, inquiries,
experiments and predictions each normalised their key in a copy of their own,
and their routes each answered an edit and a delete in a copy of their own. A
fix to either had to be made six times.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

import crud
from routers.dependencies import deleted_or_404, updated_or_404

BACKEND = Path(__file__).resolve().parents[1]


def test_a_key_ignores_case_and_spacing_and_keeps_255_characters():
    assert (
        crud.normalise_key("  Sleeps  BADLY\nbefore races ")
        == "sleeps badly before races"
    )
    assert len(crud.normalise_key("x" * 300)) == 255


def test_the_key_is_made_in_one_place():
    """A seventh copy would be the drift this issue is about."""
    copies = [
        str(path.relative_to(BACKEND))
        for path in sorted(BACKEND.rglob("*.py"))
        if "tests" not in path.parts
        and ".casefold().split())[:255]" in path.read_text(encoding="utf-8")
    ]

    assert copies == ["crud/keys.py"]


async def _value(value):
    return value


async def _refused():
    raise ValueError("statement must not be empty")


@pytest.mark.asyncio
async def test_an_edit_answers_the_row_or_why_not():
    row = object()

    assert await updated_or_404(_value(row)) is row
    with pytest.raises(HTTPException) as missing:
        await updated_or_404(_value(None))
    with pytest.raises(HTTPException) as refused:
        await updated_or_404(_refused())

    assert missing.value.status_code == 404
    assert (refused.value.status_code, refused.value.detail) == (
        422,
        "statement must not be empty",
    )


@pytest.mark.asyncio
async def test_a_delete_answers_204_or_404():
    assert (await deleted_or_404(_value(True))).status_code == 204
    with pytest.raises(HTTPException) as missing:
        await deleted_or_404(_value(False))

    assert missing.value.status_code == 404
