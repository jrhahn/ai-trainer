"""What /ask-trainer answers, pinned while ask_trainer is split up (ai-trainer-ops#247).

ask_trainer reads what the athlete's message says before it asks the model:
availability, illness, races, goals, day preferences, heart-rate medication,
an FTP. Splitting it into services must not change a byte of what it answers.
This test sends one athlete's messages in order, with the model mocked and the
date fixed, and compares each answer with the one recorded before the split.

To record the answers again: ``GOLDEN_247_RECORD=1 uv run pytest`` this file,
and say in the pull request why they changed. Once the three parts of #247 are
in, this test can go: the chat's own tests hold its behaviour.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

import crud
from auth import decode_token
from services import dates
from tests.conftest import TestSessionLocal

SNAPSHOT = Path(__file__).parent / "snapshots" / "golden" / "ask_trainer_247.json"
# A Wednesday. Every date the chat resolves is counted from it.
NOW = datetime(2026, 11, 4, 9, 0, tzinfo=timezone.utc)

MESSAGES = (
    "I can only train on Monday and Thursday now. My FTP is 255 W.",
    "Ich bin krank, Halsweh und Schnupfen.",
    "Bin wieder gesund.",
    "I have a race in 3 weeks.",
    "Also, Monday is generally better than tuesday",
    "Mein Ziel ist 4000 hm an einem Tag.",
    "Ich nehme Betablocker.",
    "I can't train next Thursday.",
)

PLAN = [
    {
        "date": f"2026-11-{day:02d}",
        "workoutType": kind,
        "title": title,
        "description": f"{title} as planned.",
        "durationMinutes": minutes,
    }
    for day, kind, title, minutes in (
        (2, "endurance", "Endurance Ride", 90),
        (3, "rest", "Rest Day", 0),
        (4, "intervals", "Threshold Intervals", 75),
        (5, "endurance", "Endurance Ride", 60),
        (6, "rest", "Rest Day", 0),
        (7, "endurance", "Long Ride", 180),
        (8, "recovery", "Recovery Spin", 45),
    )
]

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz is None else NOW.astimezone(tz)


def _normalised(value):
    """The answer without what differs on every run, ids and the clock's times,
    and without what an unrelated change moves: the line a "coach saw" item
    links to, and the prompt text it quotes (the prompt snapshots hold that)."""
    if isinstance(value, dict):
        return {
            key: _normalised(item)
            for key, item in sorted(value.items())
            if key not in ("id", "line")
            and not key.endswith("At")
            and not key.endswith("Id")
            and not (key == "text" and "source" in value)
        }
    if isinstance(value, list):
        return [_normalised(item) for item in value]
    if isinstance(value, str):
        return _UUID.sub("<id>", value)
    return value


@pytest.mark.asyncio
async def test_the_chat_answers_as_it_did_before_the_split(
    client, auth_headers, mock_ai_service, monkeypatch
):
    monkeypatch.setattr(dates, "datetime", _FixedDatetime)
    mock_ai_service["ask_trainer"].return_value = {
        "response": "Noted. I moved Friday's session.",
        "plan_updates": [
            {
                "date": "2026-11-06",
                "workoutType": "endurance",
                "title": "Endurance Ride",
                "description": "Steady Z2.",
                "durationMinutes": 60,
            }
        ],
        "sources": [],
    }
    user_id = decode_token(auth_headers["Authorization"].split(" ", 1)[1])
    async with TestSessionLocal() as db:
        await crud.upsert_training_plan(db, user_id, PLAN)
        await db.commit()

    answers = []
    for message in MESSAGES:
        response = await client.post(
            "/api/v1/ai/ask-trainer",
            headers={**auth_headers, "X-App-Timezone": "Europe/Berlin"},
            json={"question": message},
        )
        answers.append(
            {
                "message": message,
                "status": response.status_code,
                "answer": _normalised(response.json()),
            }
        )

    if os.environ.get("GOLDEN_247_RECORD") == "1":
        SNAPSHOT.write_text(json.dumps(answers, indent=1, ensure_ascii=False) + "\n")
    assert answers == json.loads(SNAPSHOT.read_text())
