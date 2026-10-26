"""The training phase every plan writer plans inside (ai-trainer-ops#64)."""

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

import models
from auth import hash_password
from services import plan_context
from services import training_phase as tp
from services.dates import app_today
from tests.conftest import TestSessionLocal


def _rides(today: date, weekly_hours: list[float], *, skip: set[date] = frozenset()):
    """Daily rides over the given weeks (oldest first), ending yesterday."""
    rows = []
    end = today - timedelta(days=1)
    for back, hours in enumerate(reversed(weekly_hours)):
        for day in range(7):
            when = end - timedelta(days=back * 7 + day)
            if when in skip or hours == 0:
                continue
            rows.append(
                SimpleNamespace(
                    activity_date=when.isoformat(),
                    duration_seconds=int(hours / 7 * 3600),
                )
            )
    return rows


OCTOBER = date(2026, 10, 11)


def test_october_without_a_race_or_a_break_proposes_transition():
    phase = tp.training_phase(OCTOBER, [], _rides(OCTOBER, [8] * 9))

    assert phase.phase == tp.TRANSITION
    section = tp.training_phase_section(phase)
    assert "days completely off" in section
    assert "proposal" in section


def test_october_after_a_break_starts_base():
    skip = {OCTOBER - timedelta(days=d) for d in (10, 11, 12, 13)}
    phase = tp.training_phase(OCTOBER, [], _rides(OCTOBER, [8] * 9, skip=skip))

    assert phase.phase == tp.BASE


def test_october_in_the_southern_hemisphere_is_spring():
    phase = tp.training_phase(OCTOBER, [], _rides(OCTOBER, [8] * 9), latitude=-33.9)

    assert phase.phase == tp.MAINTENANCE


def test_no_history_never_proposes_a_break():
    assert tp.training_phase(OCTOBER, [], []).phase == tp.BASE


def test_race_distance_decides_the_phase():
    rows = _rides(OCTOBER, [8] * 9)
    cases = {5: tp.TAPER, 30: tp.PEAK, 12 * 7: tp.BUILD, 20 * 7: tp.BASE}
    for days, expected in cases.items():
        race = (OCTOBER + timedelta(days=days)).isoformat()
        assert tp.training_phase(OCTOBER, [race], rows).phase == expected, days


def test_a_race_beyond_the_horizon_leaves_the_season_in_charge():
    race = OCTOBER + timedelta(days=tp.RACE_HORIZON_DAYS + 1)
    assert (
        tp.training_phase(OCTOBER, [race], _rides(OCTOBER, [8] * 9)).phase
        == tp.TRANSITION
    )


def test_just_after_a_race_is_transition_unless_the_next_is_close():
    june = date(2026, 6, 10)
    rows = _rides(june, [8] * 9)
    assert tp.training_phase(june, ["2026-06-06"], rows).phase == tp.TRANSITION
    assert tp.training_phase(june, ["2026-06-06", "2026-07-01"], rows).phase == tp.PEAK


def test_three_loading_weeks_make_the_next_one_a_recovery_week():
    june = date(2026, 6, 10)
    rows = _rides(june, [8, 8, 8, 8, 5, 8, 9, 10])
    phase = tp.training_phase(june, [], rows)

    assert phase.rhythm.loading_weeks == 3
    assert phase.rhythm.recovery_due
    # The season plan says which calendar week is the recovery week (#98).
    assert "recovery week" not in tp.training_phase_section(phase)


def test_right_after_a_light_week_load_may_rise():
    june = date(2026, 6, 10)
    rhythm = tp.load_rhythm(_rides(june, [8, 8, 8, 8, 8, 8, 8, 5]), june)

    assert rhythm.loading_weeks == 0
    assert not rhythm.recovery_due


def test_empty_weeks_are_recovery_not_loading():
    """Back after a break: the empty weeks were the rest, so the count restarts."""
    june = date(2026, 6, 10)
    rhythm = tp.load_rhythm(_rides(june, [8, 8, 8, 0, 0, 0, 0, 8]), june)

    assert rhythm.loading_weeks == 1
    assert not rhythm.recovery_due


def test_after_an_empty_week_the_restart_is_gentle():
    june = date(2026, 6, 10)
    phase = tp.training_phase(june, [], _rides(june, [8, 8, 8, 8, 8, 8, 8, 0]))
    section = tp.training_phase_section(phase)

    assert "Restart gently" in section
    assert "Load may rise" not in section


def test_a_new_account_starts_with_a_moderate_week():
    section = tp.training_phase_section(tp.training_phase(OCTOBER, [], []))

    assert "no training on record in the last 8 weeks" in section
    assert "Keep the first 7 days moderate" in section


@pytest.mark.parametrize(
    ("race", "expected"),
    [
        (OCTOBER + timedelta(days=7), tp.TAPER),
        (OCTOBER - timedelta(days=5), tp.TRANSITION),
    ],
)
def test_every_phase_keeps_the_new_account_week_moderate(race, expected):
    # The dashboard promises this to any account with no activity (#89).
    phase = tp.training_phase(OCTOBER, [race], [])

    assert phase.phase == expected
    assert "Keep the first 7 days moderate" in tp.training_phase_section(phase)


def test_rides_on_record_end_the_moderate_first_week():
    june = date(2026, 6, 10)
    rides = _rides(june, [0, 0, 0, 0, 0, 0, 0, 3])
    section = tp.training_phase_section(tp.training_phase(june, [], rides))

    assert "Keep the first 7 days moderate" not in section


def test_short_history_makes_no_claim_about_the_rhythm():
    june = date(2026, 6, 10)
    rhythm = tp.load_rhythm(_rides(june, [8, 8]), june)

    assert rhythm.loading_weeks is None
    assert not rhythm.recovery_due


def test_maintenance_focus_rotates_between_blocks():
    june = date(2026, 6, 10)
    foci = {
        tp.training_phase(june + timedelta(days=28 * i), [], []).focus for i in range(3)
    }

    assert len(foci) == 3


def test_today_is_not_counted():
    """An incomplete day would make every current week look light."""
    june = date(2026, 6, 10)
    rows = _rides(june, [8] * 8) + [
        SimpleNamespace(activity_date=june.isoformat(), duration_seconds=36000)
    ]
    assert (
        tp.load_rhythm(rows, june).weekly_hours[-1]
        == tp.load_rhythm(_rides(june, [8] * 8), june).weekly_hours[-1]
    )


# ---------------------------------------------------------------------------
# Wiring: what reaches a plan writer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_plan_writer_is_told_the_phase():
    async with TestSessionLocal() as db:
        user = models.User(
            email="phase-wiring@example.com",
            name="Rider",
            hashed_password=hash_password("Str0ng!Pass"),
            is_onboarded=True,
            ai_provider="gemini",
        )
        db.add(user)
        await db.flush()
        race = app_today() + timedelta(days=12 * 7)
        db.add(
            models.RaceEvent(
                user_id=user.id,
                date=race.isoformat(),
                distance_km=120,
                elevation_m=2500,
            )
        )
        await db.commit()
        user_id = user.id

    async with TestSessionLocal() as db:
        context = await plan_context.plan_writer_context(db, user_id, [])
        regen = await plan_context.training_phase_section_for_user(
            db, user_id, app_today()
        )

    assert "Phase: build" in context.coherence
    assert race.isoformat() in context.coherence
    assert regen in context.coherence


@pytest.mark.asyncio
async def test_a_full_regeneration_is_told_the_phase(
    client, auth_headers, mock_ai_service
):
    """The path that would otherwise undo the nightly run's phase."""
    response = await client.post(
        "/api/v1/ai/generate-plan", headers=auth_headers, json={}
    )

    assert response.status_code == 200
    section = mock_ai_service["generate_training_plan"].call_args.kwargs[
        "athlete_model_section"
    ]
    assert section.startswith("Training phase")
