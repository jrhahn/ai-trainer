"""The first rides test the weekly volume a new athlete was assumed to ride
(ai-trainer-ops#159)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

import crud
import models
from services import learning_pipeline, starting_hypothesis_check as check
from tests.conftest import TestSessionLocal

TODAY = date(2026, 10, 20)
NOW = datetime(2026, 10, 20, 18, tzinfo=timezone.utc)
INF = float("inf")


@pytest.mark.parametrize(
    ("statement", "expected"),
    [
        ("Has ridden 3 to 6 hours a week over the last two months", (3.0, 6.0)),
        ("Rides 6–3 h a week", (3.0, 6.0)),
        ("Has ridden under 3 hours a week", (0.0, 3.0)),
        ("Trains less than 2,5 hours weekly", (0.0, 2.5)),
        ("Has ridden over 6 hours a week", (6.0, INF)),
        ("Rides more than 8 h per week", (8.0, INF)),
        ("Has trained to a structured plan before", None),
    ],
)
def test_the_stated_range_is_read_off_the_statement(statement, expected):
    assert check.stated_range(statement) == expected


def _rides(*pairs: tuple[int, float]) -> list[tuple[str, int]]:
    """``(days ago, hours)`` pairs as ``(activity_date, duration_seconds)``."""
    return [
        ((TODAY - timedelta(days=ago)).isoformat(), int(hours * 3600))
        for ago, hours in pairs
    ]


def test_the_weeks_count_from_the_first_ride_in_the_window():
    # Two weeks of riding, 4 h each: 4 h a week, not 2 over four weeks.
    rides = _rides((13, 2), (10, 2), (6, 2), (2, 2))

    assert check.weekly_hours(rides, TODAY) == pytest.approx(4.0)


@pytest.mark.parametrize(
    "rides",
    [
        _rides((13, 3), (2, 3)),  # two rides
        _rides((9, 2), (5, 2), (1, 2)),  # ten days
        _rides((40, 9), (35, 9), (30, 9)),  # outside the window
    ],
)
def test_too_little_riding_says_nothing(rides):
    assert check.weekly_hours(rides, TODAY) is None


def test_a_ride_with_no_readable_date_is_skipped():
    rides = [("someday", 7200), *_rides((13, 2), (6, 2), (1, 2))]

    assert check.weekly_hours(rides, TODAY) == pytest.approx(3.0)


@pytest.mark.parametrize(
    ("actual", "stated", "german", "text"),
    [
        (1.6, (3.0, 6.0), True, "etwa 1,5 Stunden pro Woche, ich bin von 3–6 Stunden"),
        (
            9.2,
            (0.0, 3.0),
            False,
            "about 9 hours a week so far, and I had assumed under 3",
        ),
        (2.0, (6.0, INF), True, "von mehr als 6 Stunden"),
    ],
)
def test_the_note_says_what_the_rides_showed(actual, stated, german, text):
    assert text in check.coach_note(actual, stated, german)


async def _athlete(
    *,
    statement="Has ridden 3 to 6 hours a week over the last two months",
    question="Wie viele Stunden pro Woche bist du zuletzt gefahren?",
):
    async with TestSessionLocal() as db:
        user = models.User(
            email="new@example.com", name="Neu", hashed_password="x", is_onboarded=True
        )
        db.add(user)
        await db.flush()
        hypothesis = await crud.propose_athlete_hypothesis(
            db,
            user.id,
            statement=statement,
            category=check.VOLUME_CATEGORY,
            confidence=0.3,
        )
        await crud.record_athlete_inquiry(
            db,
            user.id,
            question=question,
            category=check.VOLUME_CATEGORY,
            options=[{"label": "3–6 h", "stance": "confirms"}],
            hypothesis_id=hypothesis.id,
        )
        await db.commit()
        return user.id


async def _ride(user_id: str, *pairs: tuple[int, float]) -> None:
    async with TestSessionLocal() as db:
        for index, (day, seconds) in enumerate(_rides(*pairs)):
            db.add(
                models.RideMetric(
                    user_id=user_id,
                    strava_activity_id=5000 + index,
                    activity_date=day,
                    sport_type="cycling",
                    duration_seconds=seconds,
                )
            )
        await db.commit()


async def _run(user_id: str) -> int:
    async with TestSessionLocal() as db:
        user = await db.get(models.User, user_id)
        changed = await check.check_starting_hypotheses(
            db, user, now=NOW, timezone_name="UTC"
        )
        await db.commit()
        return changed


async def _state(user_id: str):
    async with TestSessionLocal() as db:
        hypothesis = await db.scalar(
            select(models.AthleteHypothesis).where(
                models.AthleteHypothesis.user_id == user_id
            )
        )
        inquiry = await db.scalar(
            select(models.AthleteInquiry).where(
                models.AthleteInquiry.user_id == user_id
            )
        )
        notes = [
            m.content
            for m in (
                await db.scalars(
                    select(models.ChatMessage).where(
                        models.ChatMessage.user_id == user_id
                    )
                )
            ).all()
        ]
        return hypothesis, inquiry, notes


@pytest.mark.asyncio
async def test_rides_far_below_the_assumption_refute_it_once_with_a_note():
    user_id = await _athlete()
    await _ride(user_id, (13, 0.5), (8, 1), (3, 1))  # 2.5 h over two weeks

    assert await _run(user_id) == 1
    hypothesis, inquiry, notes = await _state(user_id)
    assert hypothesis.status == "refuted"
    assert inquiry.status == "answered"
    [note] = notes
    assert note.startswith(
        "Deine ersten Fahrten zeigen etwas anderes als meine Annahme"
    )
    assert "etwa 1,5 Stunden pro Woche" in note

    # Idempotent: refuted is no longer open, so nothing is said twice.
    assert await _run(user_id) == 0
    assert len((await _state(user_id))[2]) == 1


@pytest.mark.asyncio
async def test_rides_in_the_range_raise_confidence_quietly():
    user_id = await _athlete()
    await _ride(user_id, (13, 2), (9, 2), (6, 2), (2, 2))  # 4 h a week

    assert await _run(user_id) == 1
    hypothesis, inquiry, notes = await _state(user_id)
    assert hypothesis.status == "proposed"
    assert hypothesis.confidence == pytest.approx(check.FITTING_CONFIDENCE)
    assert inquiry.status == "pending" and notes == []
    assert await _run(user_id) == 0  # nothing new to say a second time


@pytest.mark.asyncio
async def test_rides_just_outside_the_range_are_not_held_against_it():
    user_id = await _athlete()
    await _ride(
        user_id, (13, 1.25), (9, 1.25), (6, 1.25), (2, 1.25)
    )  # 2.5 h: 17 % below 3

    assert await _run(user_id) == 0
    hypothesis, _, notes = await _state(user_id)
    assert hypothesis.status == "proposed" and notes == []


@pytest.mark.asyncio
async def test_an_open_ended_assumption_is_refuted_only_from_below():
    user_id = await _athlete(
        statement="Has ridden over 6 hours a week over the last two months",
        question="How many hours a week do you ride?",
    )
    await _ride(user_id, (13, 10), (6, 10), (2, 10))

    assert await _run(user_id) == 1
    hypothesis, _, notes = await _state(user_id)
    assert hypothesis.status == "proposed" and notes == []


@pytest.mark.asyncio
async def test_the_note_is_in_the_athletes_own_language_first():
    user_id = await _athlete(question="How many hours a week did you ride recently?")
    async with TestSessionLocal() as db:
        await crud.create_chat_message(
            db,
            user_id,
            role="user",
            content="Ich bin heute müde und habe keine Zeit",
            timestamp=NOW.isoformat(),
        )
        await db.commit()
    await _ride(user_id, (13, 0.5), (8, 0.5), (3, 0.5))

    await _run(user_id)

    assert (await _state(user_id))[2][-1].startswith("Deine ersten Fahrten")


@pytest.mark.asyncio
async def test_without_an_open_assumption_or_a_range_nothing_happens():
    user_id = await _athlete(statement="Has ridden a lot lately")
    await _ride(user_id, (13, 0.2), (8, 0.2), (3, 0.2))

    assert await _run(user_id) == 0
    async with TestSessionLocal() as db:
        other = models.User(
            email="none@example.com",
            name="Leer",
            hashed_password="x",
            is_onboarded=True,
        )
        db.add(other)
        await db.commit()
        assert await check.check_starting_hypotheses(db, other, now=NOW) == 0


@pytest.mark.asyncio
async def test_the_learning_step_runs_the_check():
    user_id = await _athlete()
    await _ride(user_id, (13, 0.5), (8, 0.5), (3, 0.5))

    async with TestSessionLocal() as db:
        user = await db.get(models.User, user_id)
        result = await learning_pipeline.run_learning_step(
            db, user, now=NOW, timezone_name="UTC"
        )
        await db.commit()

    assert result.starting_hypotheses == 1 and result.changed
    assert (await _state(user_id))[0].status == "refuted"


class _Field:
    def __init__(self, name, value):
        self.name, self.value = name, value


class _Record:
    def __init__(self, fields):
        self._fields = fields

    def __iter__(self):
        return iter(self._fields)


class _AgedFit:
    """Stands in for fitparse. The bytes are ``<age in days>:<minutes>``."""

    def __init__(self, raw_stream, *args, **kwargs):
        from services.dates import app_today

        age, minutes = (int(part) for part in raw_stream.read().decode().split(":"))
        day = app_today() - timedelta(days=age)
        self.start = datetime(day.year, day.month, day.day, 8, tzinfo=timezone.utc)
        self.seconds, self.serial = minutes * 60, f"aged-{age}"

    def parse(self):
        return None

    def get_messages(self, msg_type):
        if msg_type == "file_id":
            return [
                _Record(
                    [
                        _Field("serial_number", self.serial),
                        _Field("time_created", self.start),
                    ]
                )
            ]
        if msg_type == "session":
            return [
                _Record(
                    [
                        _Field("sport", "cycling"),
                        _Field("total_elapsed_time", self.seconds),
                        _Field("start_time", self.start),
                    ]
                )
            ]
        return []


@pytest.mark.asyncio
async def test_an_upload_tests_the_assumption_too(
    client, auth_headers, mock_ai_service, monkeypatch
):
    """A new athlete without a connected service brings rides by upload (#157)."""
    import fitparse

    monkeypatch.setattr(fitparse, "FitFile", _AgedFit)
    async with TestSessionLocal() as db:
        user = await db.scalar(select(models.User))
        hypothesis = await crud.propose_athlete_hypothesis(
            db,
            user.id,
            statement="Has ridden over 6 hours a week",
            category=check.VOLUME_CATEGORY,
        )
        await crud.record_athlete_inquiry(
            db,
            user.id,
            question="How many hours a week do you ride?",
            category=check.VOLUME_CATEGORY,
            hypothesis_id=hypothesis.id,
        )
        await db.commit()

    files = [
        ("files", (f"{age}.fit", f"{age}:30".encode(), "application/octet-stream"))
        for age in (13, 8, 3)
    ]
    response = await client.post(
        "/api/v1/users/me/upload-fit/bulk", headers=auth_headers, files=files
    )

    assert response.json()["imported"] == 3
    async with TestSessionLocal() as db:
        settled = await db.scalar(select(models.AthleteHypothesis))
        [note] = (
            await db.scalars(
                select(models.ChatMessage).where(models.ChatMessage.role == "assistant")
            )
        ).all()
    assert settled.status == "refuted"
    assert note.content.startswith("Your first rides tell a different story")


@pytest.mark.asyncio
async def test_a_failing_check_does_not_fail_the_upload(
    client, auth_headers, mock_ai_service, monkeypatch
):
    import fitparse

    async def broken(*args, **kwargs):
        raise RuntimeError("check broke")

    monkeypatch.setattr(fitparse, "FitFile", _AgedFit)
    monkeypatch.setattr(check, "check_starting_hypotheses", broken)

    response = await client.post(
        "/api/v1/users/me/upload-fit/bulk",
        headers=auth_headers,
        files=[("files", ("1.fit", b"1:30", "application/octet-stream"))],
    )

    assert response.status_code == 200 and response.json()["imported"] == 1
