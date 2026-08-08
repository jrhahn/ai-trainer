"""The pile drains, the ceilings hold, and both leave a trace (#581).

``test_uncertainty_lifecycle.py`` pins the rule; this runs it against the
database, because the failure being fixed was never in the arithmetic — it was
that nothing ever called anything like it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import crud
import models
from services import uncertainty_lifecycle
from tests.conftest import TestSessionLocal

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
LONG_AGO = NOW - timedelta(days=200)


async def _current_user_id(client, auth_headers) -> str:
    response = await client.get("/api/v1/users/me", headers=auth_headers)
    return response.json()["id"]


async def _propose(user_id: str, statement: str, **kwargs):
    async with TestSessionLocal() as db:
        row = await crud.propose_athlete_hypothesis(
            db, user_id, statement=statement, **kwargs
        )
        await db.commit()
        return row


async def _sweep(user_id: str, *, now: datetime = NOW) -> dict[str, int]:
    async with TestSessionLocal() as db:
        expired = await crud.expire_stale_uncertainties(db, user_id, now=now)
        await db.commit()
        return expired


async def _events(user_id: str) -> list[models.AthleteUncertaintyEvent]:
    async with TestSessionLocal() as db:
        return await crud.list_uncertainty_events(db, user_id)


async def _hypotheses(user_id: str, *, include_resolved: bool = False):
    async with TestSessionLocal() as db:
        return await crud.list_athlete_hypotheses(
            db, user_id, include_resolved=include_resolved
        )


# --- The pile drains -------------------------------------------------------


@pytest.mark.asyncio
async def test_a_hypothesis_nothing_came_back_to_stops_being_proposed(
    client, auth_headers
):
    user_id = await _current_user_id(client, auth_headers)
    await _propose(
        user_id,
        "Strength the day before suppresses HR response",
        category="fatigue_response",
        observed_at=LONG_AGO,
    )

    expired = await _sweep(user_id)

    assert expired == {uncertainty_lifecycle.CHANNEL_HYPOTHESIS: 1}
    assert await _hypotheses(user_id) == []
    # Not deleted: the row survives so "which uncertainties actually resolved?"
    # stays answerable.
    stored = await _hypotheses(user_id, include_resolved=True)
    assert len(stored) == 1
    assert stored[0].status == uncertainty_lifecycle.STATUS_EXPIRED


@pytest.mark.asyncio
async def test_expiry_is_auditable(client, auth_headers):
    user_id = await _current_user_id(client, auth_headers)
    await _propose(
        user_id,
        "Sweet-spot blocks raise threshold within three weeks",
        category="general",
        observed_at=LONG_AGO,
    )

    await _sweep(user_id)

    events = await _events(user_id)
    assert len(events) == 1
    assert events[0].event == uncertainty_lifecycle.EVENT_EXPIRED
    assert events[0].channel == uncertainty_lifecycle.CHANNEL_HYPOTHESIS
    # The statement is denormalised so the trace outlives the record.
    assert "Sweet-spot blocks" in events[0].statement
    # The metric that says whether falsifiability improved: 96 % died at 1.
    assert events[0].evidence_count == 1
    assert events[0].age_days == 200
    assert "Never observed a second time" in events[0].reason


@pytest.mark.asyncio
async def test_a_fresh_hypothesis_survives_the_sweep(client, auth_headers):
    user_id = await _current_user_id(client, auth_headers)
    await _propose(
        user_id,
        "Long rides in heat cost more than the power suggests",
        category="general",
        observed_at=NOW - timedelta(days=2),
    )

    assert await _sweep(user_id) == {}
    assert len(await _hypotheses(user_id)) == 1


@pytest.mark.asyncio
async def test_a_verdict_the_athlete_reached_never_expires(client, auth_headers):
    """Confirmed and refuted mean someone ruled. Verdicts do not go stale."""
    user_id = await _current_user_id(client, auth_headers)
    row = await _propose(
        user_id,
        "Morning sessions produce better power than evening",
        category="general",
        observed_at=LONG_AGO,
    )
    async with TestSessionLocal() as db:
        await crud.update_athlete_hypothesis(db, user_id, row.id, status="confirmed")
        await db.commit()

    assert await _sweep(user_id) == {}
    stored = await _hypotheses(user_id, include_resolved=True)
    assert stored[0].status == "confirmed"


@pytest.mark.asyncio
async def test_the_deterministic_writers_are_not_swept(client, auth_headers):
    """``hypothesis_engine`` re-derives its set every pass and retires what the
    model stops supporting. Expiring one here would fight that machinery."""
    user_id = await _current_user_id(client, auth_headers)
    await _propose(
        user_id,
        "Sustained-power limiter at 20 minutes",
        category="performance_model",
        observed_at=LONG_AGO,
    )

    assert await _sweep(user_id) == {}
    assert len(await _hypotheses(user_id)) == 1


@pytest.mark.asyncio
async def test_the_sweep_is_idempotent(client, auth_headers):
    user_id = await _current_user_id(client, auth_headers)
    await _propose(
        user_id, "Cadence drops before a bad session", observed_at=LONG_AGO
    )

    assert await _sweep(user_id) == {uncertainty_lifecycle.CHANNEL_HYPOTHESIS: 1}
    assert await _sweep(user_id) == {}
    assert len(await _events(user_id)) == 1


@pytest.mark.asyncio
async def test_fresh_evidence_revives_an_expired_hypothesis(client, auth_headers):
    """A claim that recurred after expiring is exactly what the ceiling exists to
    make room for, so it comes back rather than being blocked."""
    user_id = await _current_user_id(client, auth_headers)
    statement = "Back-to-back hard days cost more than the plan assumes"
    await _propose(user_id, statement, observed_at=LONG_AGO)
    await _sweep(user_id)

    revived = await _propose(user_id, statement, observed_at=NOW)

    assert revived is not None
    assert revived.status == "proposed"
    assert revived.evidence_count == 2


# --- The ceiling holds -----------------------------------------------------


@pytest.mark.asyncio
async def test_a_full_channel_declines_a_new_hypothesis(client, auth_headers):
    user_id = await _current_user_id(client, auth_headers)
    ceiling = uncertainty_lifecycle.HYPOTHESIS_POLICY.ceiling
    for index in range(ceiling):
        assert (
            await _propose(
                user_id,
                f"Recurring pattern number {index}",
                observed_at=NOW,
                max_open=ceiling,
            )
            is not None
        )

    declined = await _propose(
        user_id, "One idea too many", observed_at=NOW, max_open=ceiling
    )

    assert declined is None
    assert len(await _hypotheses(user_id)) == ceiling


@pytest.mark.asyncio
async def test_being_declined_is_auditable(client, auth_headers):
    """Without this, a ceiling is invisible: there is no way to tell "the coach
    had nothing to say" from "the coach was not allowed to"."""
    user_id = await _current_user_id(client, auth_headers)
    ceiling = uncertainty_lifecycle.HYPOTHESIS_POLICY.ceiling
    for index in range(ceiling):
        await _propose(user_id, f"Pattern {index}", observed_at=NOW, max_open=ceiling)

    await _propose(user_id, "One idea too many", observed_at=NOW, max_open=ceiling)

    events = await _events(user_id)
    assert len(events) == 1
    assert events[0].event == uncertainty_lifecycle.EVENT_DECLINED
    assert events[0].statement == "One idea too many"
    assert events[0].record_id is None
    assert str(ceiling) in events[0].reason


@pytest.mark.asyncio
async def test_a_full_channel_still_accepts_evidence_for_what_it_holds(
    client, auth_headers
):
    """The whole point is to make evidence accrue on what is already there, so a
    full channel must still be able to observe one of its own members again."""
    user_id = await _current_user_id(client, auth_headers)
    ceiling = uncertainty_lifecycle.HYPOTHESIS_POLICY.ceiling
    for index in range(ceiling):
        await _propose(user_id, f"Pattern {index}", observed_at=NOW, max_open=ceiling)

    again = await _propose(user_id, "Pattern 0", observed_at=NOW, max_open=ceiling)

    assert again is not None
    assert again.evidence_count == 2


@pytest.mark.asyncio
async def test_a_writer_that_passes_no_ceiling_is_unaffected(client, auth_headers):
    """The deterministic generators pass none — they are bounded by construction
    and retire their own set."""
    user_id = await _current_user_id(client, auth_headers)
    ceiling = uncertainty_lifecycle.HYPOTHESIS_POLICY.ceiling
    for index in range(ceiling):
        await _propose(user_id, f"Pattern {index}", observed_at=NOW, max_open=ceiling)

    unbounded = await _propose(
        user_id,
        "Sustained-power limiter at 20 minutes",
        category="performance_model",
        observed_at=NOW,
    )

    assert unbounded is not None


# --- The other two channels ------------------------------------------------


@pytest.mark.asyncio
async def test_open_questions_have_a_ceiling_and_an_end(client, auth_headers):
    user_id = await _current_user_id(client, auth_headers)
    ceiling = uncertainty_lifecycle.OPEN_QUESTION_POLICY.ceiling

    async with TestSessionLocal() as db:
        for index in range(ceiling):
            row = await crud.record_athlete_open_question(
                db,
                user_id,
                question=f"Open question {index}?",
                observed_at=LONG_AGO,
                max_open=ceiling,
            )
            assert row is not None
        declined = await crud.record_athlete_open_question(
            db, user_id, question="One question too many?", max_open=ceiling
        )
        await db.commit()
    assert declined is None

    expired = await _sweep(user_id)
    assert expired == {uncertainty_lifecycle.CHANNEL_OPEN_QUESTION: ceiling}

    async with TestSessionLocal() as db:
        assert await crud.list_athlete_open_questions(db, user_id) == []


@pytest.mark.asyncio
async def test_experiments_have_a_ceiling_and_an_end(client, auth_headers):
    user_id = await _current_user_id(client, auth_headers)
    ceiling = uncertainty_lifecycle.EXPERIMENT_POLICY.ceiling

    async with TestSessionLocal() as db:
        for index in range(ceiling):
            row = await crud.suggest_athlete_experiment(
                db,
                user_id,
                question=f"Uncertainty {index}?",
                protocol=f"Protocol {index}",
                observed_at=LONG_AGO,
                max_open=ceiling,
            )
            assert row is not None
        declined = await crud.suggest_athlete_experiment(
            db,
            user_id,
            question="One more?",
            protocol="One protocol too many",
            max_open=ceiling,
        )
        await db.commit()
    assert declined is None

    expired = await _sweep(user_id)
    assert expired == {uncertainty_lifecycle.CHANNEL_EXPERIMENT: ceiling}

    async with TestSessionLocal() as db:
        assert await crud.list_athlete_experiments(db, user_id) == []


@pytest.mark.asyncio
async def test_a_completed_experiment_is_not_expired(client, auth_headers):
    """The athlete ran it. That is a result, not an abandonment."""
    user_id = await _current_user_id(client, auth_headers)
    async with TestSessionLocal() as db:
        row = await crud.suggest_athlete_experiment(
            db,
            user_id,
            question="Do both bikes differ?",
            protocol="Ride both with the same pedals",
            observed_at=LONG_AGO,
        )
        await crud.update_athlete_experiment(
            db, user_id, row.id, status="completed"
        )
        await db.commit()

    assert await _sweep(user_id) == {}
