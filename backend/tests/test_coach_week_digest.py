"""One athlete's week for the weekly coach review (ai-trainer-ops#143)."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest
import pytest_asyncio

import crud
import models
from scripts import coach_week_digest as cwd
from services.plan_pipeline import PLAN_SOURCES
from tests.conftest import TestSessionLocal

# A Sunday. In Berlin (CEST, UTC+2) the window runs from 2026-10-11 22:00 UTC
# to 2026-10-18 22:00 UTC.
UNTIL = date(2026, 10, 18)


def _utc(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=timezone.utc)


async def _seed(db) -> dict[str, str]:
    owner = await crud.create_user(
        db, email="Owner@Example.com", name="Owner Name", hashed_password="x"
    )
    owner.timezone_name = "Europe/Berlin"
    owner.current_ftp = 251
    other = await crud.create_user(
        db, email="other@example.com", name="Other Name", hashed_password="x"
    )
    ids = {"owner": owner.id, "other": other.id}

    def message(role, text, at, user=owner, **extra):
        row = models.ChatMessage(
            user_id=user.id,
            role=role,
            content=text,
            timestamp=extra.pop("timestamp", at.isoformat()),
            created_at=at,
            **extra,
        )
        db.add(row)
        return row

    def change(batch, source, day, at, new, user=owner, **extra):
        db.add(
            models.PlanDayHistory(
                user_id=user.id,
                date=day,
                slot=0,
                batch_id=batch,
                new_day=new,
                source=source,
                applied=True,
                recorded_at=at,
                **extra,
            )
        )

    # Seven messages before the window; the digest keeps the last six.
    context = [
        message(
            "user" if i % 2 == 0 else "assistant",
            f"context {i}",
            _utc(2026, 10, 10, 10, i),
            timestamp=f"ctx-ts-{i}",
        )
        for i in range(7)
    ]
    # 00:30 on Monday in Berlin, 22:30 on Sunday in UTC: inside the window.
    message("user", "Monday just after midnight", _utc(2026, 10, 11, 22, 30))

    message("user", "Can I skip Thursday?", _utc(2026, 10, 14, 8, 0))
    change(
        "chat-batch",
        "coach_chat",
        "2026-10-16",
        _utc(2026, 10, 14, 8, 0, 3),
        {"workoutType": "rest", "durationMinutes": 0},
        old_day={"workoutType": "intervals", "durationMinutes": 60},
        reason="athlete asked",
    )
    reply = message(
        "assistant",
        "Thursday is now a rest day.",
        _utc(2026, 10, 14, 8, 0, 5),
        plan_update_count=1,
    )

    change(
        "night-batch",
        "nightly_maintenance",
        "2026-10-17",
        _utc(2026, 10, 15, 2, 0),
        {"workoutType": "endurance", "durationMinutes": 120},
    )
    summary = models.PlanChangeSummary(
        user_id=owner.id,
        batch_id="night-batch",
        source="nightly_maintenance",
        summary="Moved the long ride to Saturday.",
        recorded_at=_utc(2026, 10, 15, 2, 0, 1),
    )
    db.add(summary)
    narrative = message(
        "assistant", "Moved the long ride to Saturday.", _utc(2026, 10, 15, 2, 0, 2)
    )

    change(
        "edit-batch",
        "user_edit",
        "2026-10-18",
        _utc(2026, 10, 16, 10, 0),
        {"workoutType": "endurance", "durationMinutes": 90},
    )
    db.add(
        models.RideMetric(
            user_id=owner.id,
            strava_activity_id=1,
            activity_date="2026-10-17",
            activity_start_datetime="2026-10-17T09:00:00",
            activity_name="Long ride",
            duration_seconds=7200,
            start_lat=52.5163,
            start_lng=13.3777,
            avg_power_w=180,
            tss=110,
            ftp_used=251,
            user_note="legs heavy",
        )
    )
    db.add(
        models.WorkoutLog(
            user_id=owner.id,
            date="2026-10-17",
            slot=0,
            actual_duration_minutes=120,
            perceived_effort=6,
            completed_at="2026-10-17T12:00:00",
            notes="ok",
        )
    )
    flags = [
        # A live reply: the client's timestamp is not the stored one.
        ("reply", "2026-10-14T08:00:05.123Z", "Thursday is now a rest day.", 9),
        # A reply from before the window, found by its stored timestamp.
        ("reply", "ctx-ts-5", "not the text", 10),
        ("day", "2026-10-16#0", "rest", 11),
    ]
    for target, ref, excerpt, hour in flags:
        db.add(
            models.WrongFlag(
                user_id=owner.id,
                target=target,
                ref=ref,
                excerpt=excerpt,
                note="I asked for Friday",
                git_sha="abc",
                created_at=_utc(2026, 10, 14, hour, 0),
            )
        )
    # 00:30 on the Monday after, in Berlin: outside.
    message("user", "Monday after the window", _utc(2026, 10, 18, 22, 30))

    message("user", "OTHER-ATHLETE message", _utc(2026, 10, 14, 8, 0), user=other)
    change(
        "other-batch",
        "coach_chat",
        "2026-10-16",
        _utc(2026, 10, 14, 8, 0, 3),
        {"workoutType": "rest"},
        user=other,
    )
    db.add(
        models.RideMetric(
            user_id=other.id,
            strava_activity_id=2,
            activity_date="2026-10-17",
            activity_name="OTHER-ATHLETE ride",
        )
    )
    await db.commit()
    ids.update(
        reply=reply.id,
        narrative=narrative.id,
        summary=summary.id,
        context_answer=context[5].id,
    )
    return ids


@pytest_asyncio.fixture
async def seeded(monkeypatch):
    async with TestSessionLocal() as db:
        ids = await _seed(db)
    monkeypatch.setattr(cwd, "async_session_maker", TestSessionLocal)
    return ids


@pytest.mark.asyncio
async def test_only_the_named_account_and_nothing_that_identifies_it(seeded):
    digest = await cwd.build("owner@example.com", UNTIL)
    text = json.dumps(digest, default=str)

    for absent in (
        "OTHER-ATHLETE",
        "other-batch",
        seeded["other"],
        seeded["owner"],
        "Owner Name",
        "xample.com",
        "52.5163",
        "13.3777",
        "start_lat",
        # A flag's note and excerpt stay on the server (models.WrongFlag).
        "I asked for Friday",
        "not the text",
        "excerpt",
    ):
        assert absent not in text, absent


@pytest.mark.asyncio
async def test_the_window_is_the_week_in_the_athletes_time_zone(seeded):
    # 07:00 on Monday in Berlin: yesterday was the Sunday the window ends on.
    digest = await cwd.build("owner@example.com", now=_utc(2026, 10, 19, 5, 0))

    assert digest["window"] == {"from": "2026-10-12", "until": "2026-10-18"}
    messages = [e for e in digest["events"] if e["kind"] == "message"]
    texts = [e["text"] for e in messages]
    assert "Monday just after midnight" in texts
    assert "Monday after the window" not in texts
    context = [e["text"] for e in messages if e.get("context")]
    assert context == [f"context {i}" for i in range(1, 7)]
    times = [e["at"] for e in digest["events"]]
    assert times == sorted(times)


@pytest.mark.asyncio
async def test_coach_actions_are_replies_and_the_coachs_own_runs(seeded):
    digest = await cwd.build("owner@example.com", UNTIL)
    actions = {a["ref"]: a for a in digest["coach_actions"]}

    assert set(actions) == {seeded["reply"], "night-batch"}
    assert actions[seeded["reply"]]["kind"] == "reply"
    assert actions[seeded["reply"]]["batches"] == ["chat-batch"]
    run = actions["night-batch"]
    assert run["kind"] == "plan_run"
    assert run["source"] == "nightly_maintenance"
    assert run["summary"] == seeded["summary"]
    assert run["message"] == seeded["narrative"]
    narrative = next(e for e in digest["events"] if e["id"] == seeded["narrative"])
    assert narrative["narrates"] == "night-batch"


@pytest.mark.asyncio
async def test_plan_changes_rides_logs_and_flags_are_in(seeded):
    digest = await cwd.build("owner@example.com", UNTIL)
    events = digest["events"]

    chat_change = next(e for e in events if e.get("batch_id") == "chat-batch")
    assert chat_change["source"] == "coach_chat"
    assert chat_change["reason"] == "athlete asked"
    assert chat_change["old"] == {"workoutType": "intervals", "durationMinutes": 60}
    assert any(e.get("batch_id") == "edit-batch" for e in events)

    plan = {(d["date"], d["slot"]): d["day"] for d in digest["plan"]}
    assert plan[("2026-10-16", 0)]["workoutType"] == "rest"
    assert plan[("2026-10-18", 0)]["durationMinutes"] == 90

    ride = next(e for e in events if e["kind"] == "ride")
    assert (ride["duration_min"], ride["tss"], ride["athlete_note"]) == (
        120,
        110,
        "legs heavy",
    )
    # 09:00 in Berlin.
    assert ride["at"] == "2026-10-17T07:00:00+00:00"
    log = next(e for e in events if e["kind"] == "workout_log")
    assert (log["minutes"], log["rpe"]) == (120, 6)

    flags = [e for e in events if e["kind"] == "wrong_flag"]
    assert [f["message_id"] for f in flags] == [
        seeded["reply"],
        seeded["context_answer"],
        None,
    ]
    assert digest["athlete"]["current_ftp"] == 251


@pytest.mark.asyncio
async def test_an_unknown_address_has_no_digest(seeded):
    assert await cwd.build("nobody@example.com", UNTIL) is None


def test_every_plan_trigger_has_a_place_in_the_review():
    groups = [cwd.COACH_RUNS, cwd.CONTEXT_SOURCES, {cwd.CHAT_SOURCE}]
    assert set(PLAN_SOURCES) == set().union(*groups)
    assert sum(len(group) for group in groups) == len(PLAN_SOURCES)


def test_main_prints_the_digest_as_json(monkeypatch, capsys):
    async def build(email, until, days):
        return {"window": {"until": str(until)}, "days": days}

    monkeypatch.setattr(cwd, "build", build)

    assert (
        cwd.main(["--user-email", "a@b.c", "--until", "2026-10-18", "--days", "3"]) == 0
    )
    assert json.loads(capsys.readouterr().out) == {
        "window": {"until": "2026-10-18"},
        "days": 3,
    }


def test_an_unknown_address_exits_2_without_naming_it(monkeypatch, capsys):
    async def nobody(*args):
        return None

    monkeypatch.setattr(cwd, "build", nobody)

    assert cwd.main(["--user-email", "nobody@example.com"]) == 2
    assert "nobody@example.com" not in capsys.readouterr().err


def test_a_window_needs_a_day(capsys):
    with pytest.raises(SystemExit):
        cwd.main(["--user-email", "a@b.c", "--days", "0"])
    assert "--days" in capsys.readouterr().err


def test_an_unknown_time_zone_falls_back_to_utc():
    assert cwd._zone("Not/AZone") is timezone.utc
    assert cwd._zone(None) is timezone.utc
    assert cwd._local_time(None, "not a date", zone=timezone.utc) is None
    assert cwd._local_time("2026-10-17T09:00:00+02:00", zone=timezone.utc) == _utc(
        2026, 10, 17, 7
    )


# Consent (ai-trainer-ops#136): with the opt-in on, no consent, no digest.


async def _consent(user_id: str, at: datetime, *, version=None, withdrawn=None) -> None:
    from services import coach_review_consent

    async with TestSessionLocal() as db:
        db.add(
            models.CoachReviewConsent(
                user_id=user_id,
                version=version or coach_review_consent.VERSION,
                given_at=at,
                withdrawn_at=withdrawn,
            )
        )
        await db.commit()


@pytest.fixture
def opt_in(monkeypatch):
    monkeypatch.setattr(cwd.settings, "coach_review_opt_in", True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "consent",
    [
        None,
        {"withdrawn": _utc(2026, 10, 5)},
        {"version": "2026-01-01"},
    ],
    ids=["none", "withdrawn", "old-version"],
)
async def test_with_the_opt_in_on_an_account_without_a_valid_consent_is_refused(
    seeded, opt_in, consent
):
    if consent is not None:
        await _consent(seeded["owner"], _utc(2026, 10, 1), **consent)

    with pytest.raises(cwd.NotConsented, match="not consented"):
        await cwd.build("owner@example.com", UNTIL)


@pytest.mark.asyncio
async def test_a_consent_older_than_the_window_reads_the_whole_week_but_no_context(
    seeded, opt_in
):
    await _consent(seeded["owner"], _utc(2026, 10, 1))

    digest = await cwd.build("owner@example.com", UNTIL)

    assert digest["window"] == {"from": "2026-10-12", "until": "2026-10-18"}
    texts = [e["text"] for e in digest["events"] if e["kind"] == "message"]
    assert "Can I skip Thursday?" in texts
    # The six messages before the window predate nothing here, but the window
    # is bounded below anyway: context is never read once the opt-in is on.
    assert not any(e.get("context") for e in digest["events"])


@pytest.mark.asyncio
async def test_nothing_from_before_the_consent_is_read(seeded, opt_in):
    # Wednesday 2026-10-14, 12:00 in Berlin: the window starts on Thursday.
    await _consent(seeded["owner"], _utc(2026, 10, 14, 10, 0))

    digest = await cwd.build("owner@example.com", UNTIL)

    assert digest["window"]["from"] == "2026-10-15"
    texts = [e["text"] for e in digest["events"] if e["kind"] == "message"]
    assert "Can I skip Thursday?" not in texts
    assert "Monday just after midnight" not in texts
    assert all((e["at"] or "") >= "2026-10-14T22:00" for e in digest["events"])
    # The plan, too, is built only from changes recorded since the consent.
    # Thursday's rest was set on Wednesday morning, before the consent.
    assert {d["date"] for d in digest["plan"]} == {"2026-10-17", "2026-10-18"}


@pytest.mark.asyncio
async def test_a_consent_newer_than_the_window_is_refused(seeded, opt_in):
    await _consent(seeded["owner"], _utc(2026, 10, 18, 9, 0))

    with pytest.raises(cwd.NotConsented, match="newer than the window"):
        await cwd.build("owner@example.com", UNTIL)


def test_a_refusal_exits_3_without_naming_the_address(monkeypatch, capsys):
    async def refuse(*args):
        raise cwd.NotConsented("the account has not consented to the coach review")

    monkeypatch.setattr(cwd, "build", refuse)

    assert cwd.main(["--user-email", "someone@example.com"]) == 3
    err = capsys.readouterr().err
    assert "not consented" in err and "someone@example.com" not in err
