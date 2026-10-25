"""The activity analysis writes its texts in the athlete's language (ai-trainer-ops#258).

Its prompt had no language rule, and its four texts (notes, lastRideFeedback,
rideInsights, loginSummary) are stored as written: the first an athlete reads
after their first sync. The prompt names the language now, the router says
which, and an analysis in the other language is asked for once more.
"""

from __future__ import annotations

import json

import pytest

from services import ai_service
from services.prompts import ATHLETE_LANGUAGE_RULE, analyse_activities_system
from tests.test_fit_history_upload_157 import _FitFile

ENGLISH = {
    "notes": "A steady week with two good rides and a solid long ride on the weekend.",
    "lastRideFeedback": "You held your power well on the climb and kept the effort even.",
    "rideInsights": "The ride on Saturday was your longest this month.",
    "loginSummary": "A good week. - Next session: an easy spin on Tuesday.",
}
GERMAN = {
    "notes": "Eine gleichmäßige Woche mit zwei guten Ausfahrten und einer langen am Wochenende.",
    "lastRideFeedback": "Du hast die Leistung am Anstieg gut gehalten und die Belastung gleichmäßig verteilt.",
    "rideInsights": "Die Ausfahrt am Samstag war deine längste in diesem Monat.",
    "loginSummary": "Eine gute Woche. - Nächste Einheit: eine lockere Runde am Dienstag.",
}
ACTIVITY = {
    "id": 99,
    "name": "Morning Ride",
    "type": "Ride",
    "distance": 40000,
    "movingTime": 3600,
    "elapsedTime": 3700,
    "totalElevationGain": 300,
    "startDate": "2026-04-15T08:00:00Z",
    "averageWatts": 200,
}


def _replies(monkeypatch, *replies: dict) -> list[str]:
    asked: list[str] = []
    queue = list(replies)

    async def fake_chat(provider, system_prompt, user_msg, **kwargs):
        asked.append(user_msg)
        return json.dumps(queue.pop(0))

    monkeypatch.setattr(ai_service, "_chat", fake_chat)
    return asked


def test_the_prompt_names_the_language_for_its_four_texts():
    assert analyse_activities_system("cycling", 250, "German").endswith(
        f"{ATHLETE_LANGUAGE_RULE} The athlete writes German: write "
        '"notes", "lastRideFeedback", "rideInsights" and "loginSummary" in German.'
    )
    assert analyse_activities_system("running").endswith(ATHLETE_LANGUAGE_RULE)


@pytest.mark.asyncio
async def test_an_analysis_in_the_other_language_is_asked_for_once_more(monkeypatch):
    asked = _replies(monkeypatch, ENGLISH, GERMAN)

    result = await ai_service.analyse_strava_activities([ACTIVITY], reply_language="German")

    assert result["lastRideFeedback"] == GERMAN["lastRideFeedback"]
    assert len(asked) == 2
    assert asked[1].endswith("Write 'notes', 'lastRideFeedback', 'rideInsights', 'loginSummary' in German.")


@pytest.mark.asyncio
async def test_the_first_analysis_stands_when_the_second_is_no_better(monkeypatch):
    asked = _replies(monkeypatch, ENGLISH, ENGLISH)

    result = await ai_service.analyse_strava_activities([ACTIVITY], reply_language="German")

    assert result["notes"] == ENGLISH["notes"] and len(asked) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(("reply", "language"), [(GERMAN, "German"), (ENGLISH, None)])
async def test_an_analysis_in_the_right_or_an_unknown_language_is_taken_at_once(monkeypatch, reply, language):
    asked = _replies(monkeypatch, reply)

    await ai_service.analyse_strava_activities([ACTIVITY], reply_language=language)

    assert len(asked) == 1


@pytest.mark.asyncio
async def test_a_fit_analysis_is_held_to_the_language_too(monkeypatch):
    asked = _replies(monkeypatch, ENGLISH, GERMAN)

    result = await ai_service.analyse_fit_activity("cycling", 60, 180, None, reply_language="German")

    assert result["rideInsights"] == GERMAN["rideInsights"] and len(asked) == 2


@pytest.mark.asyncio
async def test_the_analysis_endpoint_passes_the_interface_language(client, auth_headers, mock_ai_service):
    response = await client.post(
        "/api/v1/ai/analyse-activities",
        headers={**auth_headers, "X-App-Language": "de"},
        json={"activities": [ACTIVITY]},
    )

    assert response.status_code == 200
    assert mock_ai_service["analyse_strava_activities"].await_args.kwargs["reply_language"] == "German"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "files"),
    [
        ("/api/v1/users/me/upload-fit", [("file", ("1.fit", b"1", "application/octet-stream"))]),
        ("/api/v1/users/me/upload-fit/bulk", [("files", ("1.fit", b"1", "application/octet-stream"))]),
    ],
)
async def test_a_fit_upload_passes_the_interface_language(
    client, auth_headers, mock_ai_service, monkeypatch, path, files
):
    import fitparse

    monkeypatch.setattr(fitparse, "FitFile", _FitFile)

    response = await client.post(path, headers={**auth_headers, "X-App-Language": "de"}, files=files)

    assert response.status_code == 200
    assert mock_ai_service["analyse_fit_activity"].await_args.kwargs["reply_language"] == "German"
