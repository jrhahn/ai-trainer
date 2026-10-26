"""An FTP the athlete states in the coach chat is offered for the profile (ai-trainer-ops#70, #129).

Set at once with a note until the owner chose a confirmation chip (2026-10-18):
it moves every power target in the plan, so a sentence read wrongly must not
change them without a tap.
"""

import pytest
from sqlalchemy import select

import models
from services.stated_ftp import extract_stated_ftp
from tests.conftest import TestSessionLocal


@pytest.mark.parametrize(
    "message, watts",
    [
        # The sentence that started it, paraphrased.
        (
            "Important: my power meter was off, my FTP is rather at 285W. Keep the rides, just adapt the zones.",
            285,
        ),
        ("Please set my FTP to 280 W", 280),
        ("Meine FTP liegt bei 290 Watt.", 290),
        ("Mein FTP ist jetzt 300w", 300),
        # Missed before (ai-trainer-ops#129): a colon, and "ca." ending the sentence.
        ("Meine FTP: 250 W", 250),
        ("Meine FTP liegt bei ca. 250 Watt", 250),
        # A condition or a negation in a clause before the FTP's own is not about
        # it (review of #934); doubt is, wherever it stands.
        ("Falls es hilft: meine FTP ist 300 W.", 300),
        ("Ich habe keine Zeit, meine FTP ist 250 W.", 250),
        ("I'm not fit, my FTP is 250 W.", 250),
    ],
)
def test_the_athletes_own_ftp_is_captured(message, watts):
    assert extract_stated_ftp(message) == watts


@pytest.mark.parametrize(
    "message",
    [
        "so that works if my ftp is about 320w?",
        "Is my FTP 300 W?",
        "my FTP was 320W before the calibration",
        "Meine FTP war 320 W.",
        "my FTP is not 320W",
        "If my FTP is 320 W I will ride more.",
        "Wenn meine FTP 320 W ist, fahre ich mehr.",
        "Früher war meine FTP 320 W.",
        "Vorher meine FTP 320 W, jetzt weiß ich es nicht.",
        "Meine FTP ist keine 320 W.",
        "Ich weiß nicht, ob meine FTP 320 W ist",
        "his FTP is 400 W",
        "Bei deiner FTP von 302 W ist das stark.",
        "my FTP is 9000 W",
        "my FTP is 285",  # no unit, no capture
        # Negation, doubt or a past value anywhere in the sentence (ai-trainer-ops#129).
        "Ich glaube nicht, dass meine FTP 300 W ist",
        "Ich bezweifle, dass meine FTP 300 W ist.",
        "Meine FTP ist 285 W, aber das war letztes Jahr.",
        "Meine FTP ist 310 W, glaube ich aber nicht.",
        "my FTP is 280 W, at least it was in spring",
        "I think my FTP is 285 W",
        # Pasted text from elsewhere, where FTP figures are someone's estimate.
        "Here is what another assistant said: " + "x " * 300 + "my FTP is 305 W",
    ],
)
def test_anything_else_is_not(message):
    assert extract_stated_ftp(message) is None


async def _ftp() -> object:
    async with TestSessionLocal() as db:
        return (await db.scalar(select(models.User))).current_ftp


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question, watts",
    [
        (
            "My power meter read high, my FTP is rather 285W. Adapt the zones please.",
            285,
        ),
        # The owner's message of 2026-10-11.
        ("Mein FTP liegt so bei 300W", 300),
        ("FTP: my FTP 310W", 310),
    ],
)
async def test_a_stated_ftp_is_offered_and_not_stored(
    client, auth_headers, mock_ai_service, question, watts
):
    before = await _ftp()

    response = await client.post(
        "/api/v1/ai/ask-trainer", headers=auth_headers, json={"question": question}
    )

    assert response.status_code == 200
    assert await _ftp() == before
    assert mock_ai_service["ask_trainer"].call_args.args[2]["currentFTP"] == before
    body = response.json()
    assert body["profileProposal"]["ftp"] == watts
    # Nothing was set, so nothing says it was, in either language.
    assert "set your FTP" not in body["response"]
    assert "FTP auf" not in body["response"]


@pytest.mark.asyncio
async def test_an_ftp_rides_along_with_the_other_profile_changes(
    client, auth_headers, mock_ai_service
):
    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={
            "question": "I can only train on Monday and Thursday now. My FTP is 255 W."
        },
    )

    proposal = response.json()["profileProposal"]
    assert proposal["ftp"] == 255
    assert proposal["trainingDays"] == ["monday", "thursday"]


@pytest.mark.asyncio
async def test_a_question_about_ftp_offers_nothing(
    client, auth_headers, mock_ai_service
):
    before = await _ftp()

    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "would it work if my ftp is about 320w?"},
    )

    assert await _ftp() == before
    assert (response.json().get("profileProposal") or {}).get("ftp") is None


@pytest.mark.asyncio
async def test_restating_the_current_ftp_offers_nothing(
    client, auth_headers, mock_ai_service
):
    current = 280
    async with TestSessionLocal() as db:
        (await db.scalar(select(models.User))).current_ftp = current
        await db.commit()

    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": f"my FTP is {current} W, as before"},
    )

    assert await _ftp() == current
    assert response.json().get("profileProposal") is None


def test_the_router_notes_have_a_german_form():
    from routers import ai as ai_router

    blocked = [
        {"constraintType": "no_training", "weekday": "friday"},
        {"constraintType": "required_workout", "weekday": "saturday"},
    ]
    override = ai_router._format_constraint_override_note(blocked, german=True)
    # The frame ("Hinweis") is the screen's now; the builder writes the sentence (#262).
    assert "Hinweis" not in override
    assert "Samstag konnte ich nicht ändern" in override
    assert "Für Freitag konnte ich kein Training einplanen" in override
    lifted = ai_router._format_constraint_lift_note(
        [{"date": "2026-10-12"}], german=True
    )
    assert "Verfügbarkeitsvorgabe für 2026-10-12 aufgehoben" in lifted
    assert ai_router._day_label({}, german=True) == "diesen Tag"
    assert "Trainingsort auf Darmstadt" in ai_router._location_note(
        "Darmstadt", german=True
    )
    assert "2026-11-30 konnte ich nicht ändern" in ai_router._unresolved_dates_note(
        "2026-11-30", german=True
    )
    assert "I could not update 2026-11-30" in ai_router._unresolved_dates_note(
        "2026-11-30", german=False
    )
