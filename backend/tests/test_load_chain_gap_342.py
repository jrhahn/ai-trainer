"""ai-trainer-ops#342: the quiet days since the newest stored ride decay the load.

The prod case: a hiking week ended on 2026-11-23 with the athlete's ATL at 55.
Four quiet days later a 40-TSS mountain-bike hour was synced. The sync seeded the
chain from the 11-23 row but not from its date, so the ride was chained as if it
came on 11-24: ATL 53 instead of 36. The coach then read that last post-ride
value as the athlete's fatigue the next day and prescribed a rest day.
"""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

import crud
import models
from domain.activity_identity import SPORT_CYCLING, SPORT_WALKING
from domain.fitness_ledger import ALPHA_ATL, apply_ctl_atl_decay, ledger_from_row
from routers import ai as ai_router
from services import analysis, prompts, ride_ingest

HOUR = 3600
HIKING_WEEK = {SPORT_CYCLING: 30.13, SPORT_WALKING: 16.29}


def _mtb(day: str, *, tss: float = 40.0, ident: int = 1) -> dict:
    return {
        "strava_activity_id": ident,
        "activity_date": day,
        "sport_type": "MountainBikeRide",
        "duration_seconds": HOUR,
        "streams": {},
        "_summary_tss": tss,
    }


def _after_the_hikes():
    return ledger_from_row(HIKING_WEEK, ctl_after=16.29, atl_after=55.0)


def test_the_days_since_the_seed_decay_the_first_ride():
    [metric] = analysis.build_ride_metrics_chain(
        [_mtb("2026-11-27")],
        250.0,
        initial_ledger=_after_the_hikes(),
        initial_date="2026-11-23",
    )
    _, expected_atl = apply_ctl_atl_decay(0.0, 55.0, 40.0, gap_days=4)
    assert metric["atl_after"] == pytest.approx(expected_atl, abs=0.01)
    assert metric["atl_after"] < 37
    # The hiking rung decayed through all four days, not one.
    assert metric["ctl_by_sport"][SPORT_WALKING] < 15.0


def test_without_a_seed_date_the_first_ride_still_follows_the_seed():
    """A first import and the frozen callers pass no date: one day, as before."""
    [metric] = analysis.build_ride_metrics_chain(
        [_mtb("2026-11-27")], 250.0, initial_ledger=_after_the_hikes()
    )
    assert metric["atl_after"] == pytest.approx(
        55.0 + ALPHA_ATL * (40.0 - 55.0), abs=0.01
    )


@pytest.mark.parametrize("one_at_a_time", [False, True])
def test_the_ingest_prices_from_the_seed_date(one_at_a_time):
    metrics, unpriced = ride_ingest._price(
        [_mtb("2026-11-27"), _mtb("2026-11-30", ident=2)],
        250.0,
        ledger=_after_the_hikes(),
        ledger_date="2026-11-23",
        one_at_a_time=one_at_a_time,
    )
    assert unpriced == []
    _, first_atl = apply_ctl_atl_decay(0.0, 55.0, 40.0, gap_days=4)
    _, second_atl = apply_ctl_atl_decay(0.0, first_atl, 40.0, gap_days=3)
    assert [m["atl_after"] for m in metrics] == [
        pytest.approx(first_atl, abs=0.01),
        pytest.approx(second_atl, abs=0.02),
    ]


def _row(day: str, *, atl: float = 36.4, sport: str = "MountainBikeRide"):
    return SimpleNamespace(
        activity_date=day,
        sport_type=sport,
        ctl_after=30.4,
        atl_after=atl,
        tsb_after=-6.0,
        ctl_by_sport={SPORT_CYCLING: 30.4, SPORT_WALKING: 15.9},
        tss=40.0,
        tss_source="provider",
        duration_seconds=HOUR,
    )


def test_the_coach_reads_the_load_as_of_today():
    section = prompts.ride_metrics_context_section(
        [_row("2026-11-24", atl=53.0)], today=date(2026, 11, 28)
    )
    decayed = 53.0 * (1 - ALPHA_ATL) ** 4
    assert (
        f"Load as of today (2026-11-28), with no activity since 2026-11-24: "
        f"ATL {decayed:.1f}" in section
    )
    assert "model estimates from past load, not a measurement" in section
    # Before the history lines, which still state the load right after each ride.
    assert section.index("Load as of today") < section.index("ATL 53.0")


@pytest.mark.parametrize(
    "newest",
    [
        _row("2026-11-28"),
        _row("2026-11-30"),
        _row("not a date"),
        SimpleNamespace(activity_date="2026-11-20", atl_after=None),
    ],
)
def test_no_line_when_there_is_nothing_to_decay(newest):
    assert prompts._load_as_of_today(newest, date(2026, 11, 28)) == ""


@pytest.mark.asyncio
async def test_the_readiness_score_decays_to_today(
    client, auth_headers, db, monkeypatch
):
    athlete = await crud.get_user_by_email(db, "rider@example.com")
    await crud.upsert_ride_metric(
        db,
        athlete.id,
        strava_activity_id=1,
        activity_date="2026-11-24",
        sport_type="MountainBikeRide",
        tss=40.0,
        ctl_after=30.4,
        atl_after=53.0,
        tsb_after=-22.6,
        ctl_by_sport={SPORT_CYCLING: 30.4, SPORT_WALKING: 15.9},
    )
    await db.commit()
    monkeypatch.setattr(ai_router, "app_today_iso", lambda **_: "2026-11-28")

    response = await client.get("/api/v1/ai/readiness-score", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["atl"] == pytest.approx(53.0 * (1 - ALPHA_ATL) ** 4, abs=0.1)


MIGRATION_PATH = (
    Path(__file__).parent.parent
    / "alembic"
    / "versions"
    / "20261128_000001_replay_load_chain_gaps.py"
)


def _migrate(engine, direction: str = "upgrade") -> None:
    spec = importlib.util.spec_from_file_location("_replay_gaps", MIGRATION_PATH)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            getattr(migration, direction)()


def test_the_migration_replays_the_stored_chain_with_its_gaps(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'gaps.db'}")
    for model in (models.User, models.RideMetric):
        model.__table__.create(engine)
    rows = [
        # (id, user, date, sport, tss, the atl the old sync stored)
        (1, "a", "2026-11-20", "Hike", 148.0, 59.5),
        (2, "a", "2026-11-23", "Hike", 18.0, 55.0),
        (3, "a", "2026-11-27", "MountainBikeRide", 40.0, 53.0),
        (4, "b", "2026-11-27", "Ride", 50.0, 99.0),
    ]
    with engine.begin() as conn:
        for user_id in ("a", "b"):
            conn.execute(
                models.User.__table__.insert(),
                {
                    "id": user_id,
                    "email": f"{user_id}@example.com",
                    "hashed_password": "x",
                },
            )
        for row_id, user_id, day, sport, tss, atl in rows:
            conn.execute(
                models.RideMetric.__table__.insert(),
                {
                    "id": str(row_id),
                    "user_id": user_id,
                    "strava_activity_id": row_id,
                    "activity_source": "intervals",
                    "external_activity_id": str(row_id),
                    "activity_date": day,
                    "sport_type": sport,
                    "tss": tss,
                    "atl_after": atl,
                },
            )

    _migrate(engine)
    _migrate(engine)  # idempotent

    with engine.connect() as conn:
        stored = dict(
            conn.execute(sa.text("SELECT id, atl_after FROM ride_metrics")).all()
        )
    _, atl_1 = apply_ctl_atl_decay(0.0, 0.0, 148.0, gap_days=1)
    _, atl_2 = apply_ctl_atl_decay(0.0, atl_1, 18.0, gap_days=3)
    _, atl_3 = apply_ctl_atl_decay(0.0, atl_2, 40.0, gap_days=4)
    assert stored["1"] == pytest.approx(atl_1, abs=0.01)
    assert stored["2"] == pytest.approx(atl_2, abs=0.01)
    assert stored["3"] == pytest.approx(atl_3, abs=0.01)
    # Each athlete's chain starts from nothing.
    assert stored["4"] == pytest.approx(50.0 * ALPHA_ATL, abs=0.01)

    _migrate(engine, "downgrade")
    with engine.connect() as conn:
        assert conn.execute(
            sa.text("SELECT atl_after FROM ride_metrics WHERE id = '3'")
        ).scalar_one() == pytest.approx(atl_3, abs=0.01)
