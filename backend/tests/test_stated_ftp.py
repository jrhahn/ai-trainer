"""An FTP the athlete states in the coach chat reaches the profile (ai-trainer-ops#70)."""

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


@pytest.mark.asyncio
async def test_a_stated_ftp_is_stored_used_this_turn_and_confirmed(
    client, auth_headers, mock_ai_service
):
    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={
            "question": "My power meter read high, my FTP is rather 285W. Adapt the zones please."
        },
    )

    assert response.status_code == 200
    async with TestSessionLocal() as db:
        user = await db.scalar(select(models.User))
    assert user.current_ftp == 285
    profile = mock_ai_service["ask_trainer"].call_args.args[2]
    assert profile["currentFTP"] == 285
    assert "I've set your FTP to 285 W" in response.json()["response"]


@pytest.mark.asyncio
async def test_a_question_about_ftp_changes_nothing_and_claims_nothing(
    client, auth_headers, mock_ai_service
):
    async with TestSessionLocal() as db:
        before = (await db.scalar(select(models.User))).current_ftp

    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "would it work if my ftp is about 320w?"},
    )

    async with TestSessionLocal() as db:
        assert (await db.scalar(select(models.User))).current_ftp == before
    assert "set your FTP" not in response.json()["response"]


@pytest.mark.asyncio
async def test_restating_the_current_ftp_writes_nothing_and_says_nothing(
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

    async with TestSessionLocal() as db:
        assert (await db.scalar(select(models.User))).current_ftp == current
    assert "set your FTP" not in response.json()["response"]


@pytest.mark.asyncio
async def test_the_note_speaks_the_athletes_language(client, auth_headers, mock_ai_service):
    # The owner's message of 2026-10-11 got an English note under a German reply.
    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "Mein FTP liegt so bei 300W"},
    )

    reply = response.json()["response"]
    assert "Ich habe deine FTP auf 300 W gesetzt" in reply
    assert "Note:" not in reply


def test_the_router_notes_have_a_german_form():
    from routers import ai as ai_router

    blocked = [
        {"constraintType": "no_training", "weekday": "friday"},
        {"constraintType": "required_workout", "weekday": "saturday"},
    ]
    override = ai_router._format_constraint_override_note(blocked, german=True)
    assert override.startswith("(Hinweis: ")
    assert "Samstag konnte ich nicht ändern" in override
    assert "Für Freitag konnte ich kein Training einplanen" in override
    lifted = ai_router._format_constraint_lift_note([{"date": "2026-10-12"}], german=True)
    assert "Verfügbarkeitsvorgabe für 2026-10-12 aufgehoben" in lifted
    assert ai_router._day_label({}, german=True) == "diesen Tag"
    assert "Trainingsort auf Darmstadt" in ai_router._location_note("Darmstadt", german=True)
    assert "2026-11-30 konnte ich nicht ändern" in ai_router._unresolved_dates_note(
        "2026-11-30", german=True
    )
    assert "I could not update 2026-11-30" in ai_router._unresolved_dates_note(
        "2026-11-30", german=False
    )


@pytest.mark.asyncio
async def test_an_undetermined_language_keeps_the_english_note(
    client, auth_headers, mock_ai_service
):
    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "FTP: my FTP 310W"},
    )

    assert "I've set your FTP to 310 W" in response.json()["response"]
