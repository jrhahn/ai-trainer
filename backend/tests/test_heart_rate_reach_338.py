"""While a medication blunts the heart rate, nothing reads the max HR given
before it (review of 2026-11-01, training A5, ai-trainer-ops#338).

The flag dropped heart-rate targets from the plan (#184), but ride analysis
still built HR zones from the onboarding max HR and put them in its prompt, the
athlete model inferred from it, and the FTP evidence gate rejected hard efforts
a blunted heart rate never "confirmed".
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

import crud
import models
from services import ai_service, heart_rate_caution
from services import athlete_model_inference as ami
from tests.conftest import TestSessionLocal
from tests.test_analysis_language_258 import ACTIVITY, ENGLISH

BACKEND = Path(__file__).resolve().parent.parent


def test_no_max_hr_for_analysis_while_the_flag_holds():
    assert heart_rate_caution.analysis_max_heart_rate(190, True) is None
    assert heart_rate_caution.analysis_max_heart_rate(190, False) == 190
    assert heart_rate_caution.analysis_max_heart_rate(190, None) == 190


def _asked(monkeypatch) -> list[str]:
    asked: list[str] = []

    async def fake_chat(provider, system_prompt, user_msg, **kwargs):
        asked.append(user_msg)
        return json.dumps(ENGLISH)

    monkeypatch.setattr(ai_service, "_chat", fake_chat)
    return asked


@pytest.mark.asyncio
async def test_ride_analysis_says_heart_rate_is_not_used(monkeypatch):
    asked = _asked(monkeypatch)

    await ai_service.analyse_strava_activities([ACTIVITY], heart_rate_blunted=True)
    await ai_service.analyse_fit_activity(
        "cycling", 60, 200, 120, heart_rate_blunted=True
    )

    assert all(heart_rate_caution.ANALYSIS_NOTE in prompt for prompt in asked)
    assert len(asked) == 2


@pytest.mark.asyncio
async def test_without_the_flag_the_analysis_prompt_has_no_such_note(monkeypatch):
    asked = _asked(monkeypatch)

    await ai_service.analyse_strava_activities([ACTIVITY], max_heart_rate=190)

    assert heart_rate_caution.ANALYSIS_NOTE not in asked[0]


async def _flag_on() -> None:
    async with TestSessionLocal() as db:
        row = await db.scalar(select(models.User))
        row.heart_rate_targets_off = True
        await db.commit()


@pytest.mark.asyncio
async def test_the_analysis_route_drops_the_max_hr_the_client_sends(
    client, auth_headers, mock_ai_service
):
    await _flag_on()

    response = await client.post(
        "/api/v1/ai/analyse-activities",
        headers=auth_headers,
        json={"activities": [ACTIVITY], "maxHeartRate": 190},
    )

    assert response.status_code == 200
    kwargs = mock_ai_service["analyse_strava_activities"].await_args.kwargs
    assert kwargs["max_heart_rate"] is None
    assert kwargs["heart_rate_blunted"] is True


@pytest.mark.asyncio
async def test_the_analysis_route_keeps_the_max_hr_without_the_flag(
    client, auth_headers, mock_ai_service
):
    response = await client.post(
        "/api/v1/ai/analyse-activities",
        headers=auth_headers,
        json={"activities": [ACTIVITY], "maxHeartRate": 190},
    )

    assert response.status_code == 200
    kwargs = mock_ai_service["analyse_strava_activities"].await_args.kwargs
    assert kwargs["max_heart_rate"] == 190
    assert kwargs["heart_rate_blunted"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", [True, False])
async def test_the_athlete_model_reads_no_max_hr_while_it_holds(db, monkeypatch, flag):
    user = await crud.create_user(
        db, email=f"hr-{flag}@example.com", name="W", hashed_password="x"
    )
    user.max_heart_rate = 175
    user.heart_rate_targets_off = flag
    seen: list[int | None] = []

    def fake_infer(metrics, *, weight_kg, max_hr, now):
        seen.append(max_hr)
        return None

    monkeypatch.setattr(ami, "infer_performance_attributes", fake_infer)

    await ami.refresh_performance_model(db, user, now=datetime.now(timezone.utc))

    assert seen == [None if flag else 175]


# Every place that hands the athlete's max HR to analysis, pricing or the
# model goes through ``analysis_max_heart_rate``: a new call site that reads
# ``user.max_heart_rate`` straight would bring the pre-medication zones back.
_RAW = re.compile(r"(?<!\.)\b(?:max_heart_rate|max_hr)\s*=\s*\w+\.max_heart_rate\b")
# Not analysis: the profile shown, offered changes, and the settings write.
_ALLOWED = {
    "routers/users/profile.py",
    "services/chat_capture.py",
}


def test_no_call_site_hands_the_raw_max_hr_to_analysis():
    raw = [
        f"{path.relative_to(BACKEND)}:{n}"
        for folder in ("services", "routers")
        for path in sorted((BACKEND / folder).rglob("*.py"))
        if str(path.relative_to(BACKEND)) not in _ALLOWED
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if _RAW.search(line)
    ]
    assert raw == []
