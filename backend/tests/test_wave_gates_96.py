"""The wave gates: a flag marked handled, and the report on a tester wave (ai-trainer-ops#96)."""

from __future__ import annotations

import importlib.util
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from httpx import AsyncClient
from sqlalchemy import select

import models
from config import settings
from scripts import wave_gates_report
from services import ai_service
from tests.conftest import TestSessionLocal

FLAG = {"target": "reply", "ref": "2026-10-14T10:00:00.000Z", "excerpt": "Ride 4 h tomorrow."}
SINCE = date(2026, 10, 26)
SIGNUP = datetime(2026, 10, 26, 18, 0, tzinfo=timezone.utc)
NUMBER_NOTE_DE = "Antwort.\n\nHinweis: 312 W in dieser Antwort ließ sich nicht aus deinen Daten ableiten."
RULE_NOTE_EN = "Reply.\n\nNote: this plan change departs from a planning rule: no long ride before a race."


@pytest.fixture
def admin_enabled(monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "super-secret")


async def _admin_headers(client: AsyncClient) -> dict[str, str]:
    resp = await client.post("/api/v1/admin/login", json={"password": "super-secret"})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _flag_id(client, auth_headers) -> str:
    await client.post("/api/v1/users/me/wrong-flags", headers=auth_headers, json=FLAG)
    async with TestSessionLocal() as session:
        return (await session.scalars(select(models.WrongFlag))).one().id


# --- marking a flag handled -------------------------------------------------


@pytest.mark.asyncio
async def test_the_admin_marks_a_flag_handled_with_a_note(client, auth_headers, admin_enabled):
    flag_id = await _flag_id(client, auth_headers)
    admin = await _admin_headers(client)

    resp = await client.put(
        f"/api/v1/admin/wrong-flags/{flag_id}/handled",
        headers=admin,
        json={"handled": True, "note": "  ops#212  "},
    )

    assert resp.status_code == 204
    flag = (await client.get("/api/v1/admin/wrong-flags", headers=admin)).json()["flags"][0]
    assert flag["handledNote"] == "ops#212"
    assert flag["handledAt"]


@pytest.mark.asyncio
async def test_a_handled_flag_can_be_opened_again(client, auth_headers, admin_enabled):
    flag_id = await _flag_id(client, auth_headers)
    admin = await _admin_headers(client)
    url = f"/api/v1/admin/wrong-flags/{flag_id}/handled"
    await client.put(url, headers=admin, json={"handled": True, "note": "answered"})

    resp = await client.put(url, headers=admin, json={"handled": False, "note": "ignored"})

    assert resp.status_code == 204
    flag = (await client.get("/api/v1/admin/wrong-flags", headers=admin)).json()["flags"][0]
    assert (flag["handledAt"], flag["handledNote"]) == (None, None)


@pytest.mark.asyncio
async def test_an_unknown_flag_is_not_found(client, admin_enabled):
    resp = await client.put(
        "/api/v1/admin/wrong-flags/nope/handled",
        headers=await _admin_headers(client),
        json={"handled": True},
    )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_only_the_admin_marks_a_flag_handled(client, auth_headers, admin_enabled):
    flag_id = await _flag_id(client, auth_headers)

    resp = await client.put(
        f"/api/v1/admin/wrong-flags/{flag_id}/handled", headers=auth_headers, json={"handled": True}
    )

    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_a_too_long_handled_note_is_refused(client, auth_headers, admin_enabled):
    flag_id = await _flag_id(client, auth_headers)

    resp = await client.put(
        f"/api/v1/admin/wrong-flags/{flag_id}/handled",
        headers=await _admin_headers(client),
        json={"handled": True, "note": "x" * 201},
    )

    assert resp.status_code == 422


# --- the note markers -------------------------------------------------------


@pytest.mark.parametrize("language", ["German", "English"])
def test_the_markers_are_in_the_notes_the_coach_adds(language):
    numbers = ai_service._Findings(numbers=["312 W"], departures=[], standing=[], claim=None)
    note = " ".join(n["text"] for n in ai_service._findings_notes(numbers, language))
    assert any(marker in note for marker in ai_service.NUMBER_NOTE_MARKERS)

    departure = ai_service.PriorDeparture(
        prior=next(iter(ai_service._PRIOR_NOTE_LABELS)), evidence="x", statement="x"
    )
    rules = ai_service._Findings(numbers=[], departures=[departure], standing=[], claim=None)
    note = " ".join(n["text"] for n in ai_service._findings_notes(rules, language))
    assert any(marker in note for marker in ai_service.RULE_NOTE_MARKERS)


@pytest.mark.parametrize("language", ["German", "English"])
def test_the_standing_rule_note_carries_the_marker_too(language):
    departure = ai_service.PriorDeparture(
        prior=next(iter(ai_service._PRIOR_NOTE_LABELS)), evidence="x", statement="x"
    )
    standing = ai_service._Findings(numbers=[], departures=[], standing=[departure], claim=None)
    note = " ".join(n["text"] for n in ai_service._findings_notes(standing, language))
    assert any(marker in note for marker in ai_service.RULE_NOTE_MARKERS)


# --- the report -------------------------------------------------------------


def _user(email: str, created: datetime) -> models.User:
    return models.User(email=email, hashed_password="x", created_at=created)


def _ride(user_id: str, n: int, created: datetime) -> models.RideMetric:
    return models.RideMetric(
        user_id=user_id,
        strava_activity_id=96000 + n,
        activity_source="intervals",
        external_activity_id=str(96000 + n),
        activity_date="2026-10-20",
        sport_type="Ride",
        created_at=created,
    )


def _reply(user_id: str, content: str, created: datetime, role: str = "assistant"):
    return models.ChatMessage(
        user_id=user_id, role=role, content=content, timestamp=created.isoformat(), created_at=created
    )


def _flag(user_id: str, target: str, created: datetime, handled: bool = False):
    return models.WrongFlag(
        user_id=user_id,
        target=target,
        ref="r",
        excerpt="secret excerpt",
        note="secret note",
        git_sha="abc",
        created_at=created,
        handled_at=created if handled else None,
    )


async def _seed_wave() -> None:
    later = SIGNUP + timedelta(days=2)
    async with TestSessionLocal() as session:
        fast = _user("fast@example.com", SIGNUP)
        slow = _user("slow@example.com", SIGNUP)
        early = _user("early@example.com", SIGNUP)
        waiting = _user("waiting@example.com", SIGNUP)
        owner = _user("Owner@Example.com", SIGNUP)
        before = _user("before@example.com", SIGNUP - timedelta(days=1))
        session.add_all([fast, slow, early, waiting, owner, before])
        await session.flush()

        # fast: a ride after 4 minutes, the plan after 9.
        session.add(_ride(fast.id, 1, SIGNUP + timedelta(minutes=4)))
        session.add(models.TrainingPlan(user_id=fast.id, plan=[], created_at=SIGNUP + timedelta(minutes=9)))
        # slow: ride and plan, but the plan after 40 minutes.
        session.add(_ride(slow.id, 2, SIGNUP + timedelta(minutes=30)))
        session.add(models.TrainingPlan(user_id=slow.id, plan=[], created_at=SIGNUP + timedelta(minutes=40)))
        # early: a plan after 5 minutes, the first ride only an hour later.
        session.add(models.TrainingPlan(user_id=early.id, plan=[], created_at=SIGNUP + timedelta(minutes=5)))
        session.add(_ride(early.id, 3, SIGNUP + timedelta(hours=1)))
        # waiting: nothing yet. owner and before: not testers, and they would pass.
        for user in (owner, before):
            session.add(models.TrainingPlan(user_id=user.id, plan=[], created_at=SIGNUP))
            session.add(_reply(user.id, NUMBER_NOTE_DE, later))
            session.add(_flag(user.id, "day", later))

        session.add_all(
            [
                _reply(fast.id, "All fine.", later),
                _reply(fast.id, NUMBER_NOTE_DE, later),
                _reply(slow.id, RULE_NOTE_EN, later),
                # The athlete quoting the note is not a reply that carried it.
                _reply(slow.id, NUMBER_NOTE_DE, later, role="user"),
                # Before the wave started.
                _reply(fast.id, NUMBER_NOTE_DE, SIGNUP - timedelta(days=3)),
                _flag(fast.id, "day", later, handled=True),
                _flag(slow.id, "reply", later),
                _flag(early.id, "day", later),
            ]
        )
        await session.commit()


@pytest.mark.asyncio
async def test_the_report_gathers_each_gate_for_the_testers_only():
    await _seed_wave()

    async with TestSessionLocal() as session:
        figures = await wave_gates_report.gather(session, SINCE, ["owner@example.com"])

    assert figures.testers == 4
    assert figures.own_data_minutes == [9, 40]
    assert figures.fast == 1
    assert (figures.plan_before_rides, figures.no_plan) == (1, 1)
    assert (figures.replies, figures.number_notes, figures.rule_notes) == (3, 1, 1)
    assert (figures.day_flags, figures.reply_flags, figures.open_flags) == (2, 1, 2)


def _measured(text: str, gate: str) -> list[str]:
    """The Measured and verdict cells of a gate's row."""
    [row] = [line for line in text.splitlines() if line.startswith(f"| {gate}")]
    return [cell.strip() for cell in row.strip("|").split("|")][1:]


@pytest.mark.asyncio
async def test_the_report_says_which_gates_hold_and_nothing_personal():
    await _seed_wave()

    async with TestSessionLocal() as session:
        figures = await wave_gates_report.gather(session, SINCE, ["owner@example.com"])
    text = wave_gates_report.render(figures, SINCE)

    assert "since 2026-10-26" in text
    assert _measured(text, "First plan") == [
        "1 of 4; minutes: 9, 40; plan before any ride: 1; no plan: 1",
        "**not met**",
    ]
    assert _measured(text, "No embarrassing plan") == [
        '"this is wrong" on a day: 2; replies noting a broken planning rule: 1',
        "**read them**",
    ]
    assert "| 1 of 3 replies | **not met** |" in text
    assert "| 2 of 3 still open | **not met** |" in text
    for private in ("example.com", "secret", "312", "Hinweis"):
        assert private not in text


def test_a_clean_wave_meets_every_gate():
    figures = wave_gates_report.WaveFigures(testers=2, own_data_minutes=[6, 11], replies=12)

    text = wave_gates_report.render(figures, SINCE)

    assert "2 of 2; minutes: 6, 11" in text
    assert "**not met**" not in text
    assert "no sign" in text
    assert "| 0 of 0 still open | met |" in text


def test_fifteen_minutes_is_not_under_fifteen():
    assert wave_gates_report.WaveFigures(testers=1, own_data_minutes=[15]).fast == 0


@pytest.mark.asyncio
async def test_a_wave_without_testers_says_so():
    async with TestSessionLocal() as session:
        figures = await wave_gates_report.gather(session, SINCE, [])

    assert figures.testers == 0
    assert wave_gates_report.render(figures, SINCE).endswith("No tester account yet.")


def test_main_needs_the_wave_start(capsys):
    with pytest.raises(SystemExit):
        wave_gates_report.main([])


@pytest.mark.asyncio
async def test_main_prints_the_report(monkeypatch, capsys):
    seen = {}

    async def fake_build(since, excluded):
        seen.update(since=since, excluded=excluded)
        return "REPORT"

    monkeypatch.setattr(wave_gates_report, "build", fake_build)
    monkeypatch.setattr(wave_gates_report.asyncio, "run", lambda coro: _run_now(coro))

    assert wave_gates_report.main(["--since", "2026-10-26", "--exclude-email", "o@x.de"]) == 0
    assert capsys.readouterr().out.strip() == "REPORT"
    assert seen == {"since": SINCE, "excluded": ["o@x.de"]}


def _run_now(coro):
    try:
        coro.send(None)
    except StopIteration as done:
        return done.value
    raise AssertionError("the coroutine awaited something")


@pytest.mark.asyncio
async def test_build_reads_the_database(monkeypatch):
    await _seed_wave()
    monkeypatch.setattr(wave_gates_report, "async_session_maker", TestSessionLocal)

    text = await wave_gates_report.build(SINCE, ["owner@example.com"])

    assert "Testers: 4." in text


def test_the_migration_adds_the_columns_once(tmp_path):
    path = Path(__file__).parent.parent / "alembic/versions/20261023_000001_wrong_flag_handled.py"
    spec = importlib.util.spec_from_file_location("_wrong_flag_handled_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'flags.db'}")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE wrong_flags (id VARCHAR(36) PRIMARY KEY)"))

    def run(direction: str) -> set[str]:
        with engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                getattr(migration, direction)()
        return {c["name"] for c in sa.inspect(engine).get_columns("wrong_flags")}

    added = {"handled_at", "handled_note"}
    assert added <= run("upgrade")
    assert added <= run("upgrade")
    assert not added & run("downgrade")
    assert not added & run("downgrade")
