"""Weather tolerances learned per athlete as confidence-scored beliefs (#495).

The pure :func:`derive_weather_preferences` correlates the conditions already
stored on each ride against outcome (intensity factor, session length) and
behaviour (rode outdoors vs moved indoors). :func:`refresh_weather_preferences`
persists the result through the same merge/decay lifecycle the performance-model
hypotheses use, and chat capture records what the athlete says about themselves.
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
from services import weather_preference
from services.weather_preference import (
    CATEGORY,
    DIMENSION_COLD,
    DIMENSION_HEAT,
    DIMENSION_RAIN,
    DIRECTION_SENSITIVE,
    DIRECTION_TOLERANT,
    RideWeather,
    derive_weather_preferences,
    extract_weather_preference_statements,
    preference_statement,
    refresh_weather_preferences,
    ride_weather_from_metric,
)
from tests.conftest import TestSessionLocal


@pytest_asyncio.fixture
async def db() -> AsyncSession:
    async with TestSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@pytest_asyncio.fixture
async def user(db: AsyncSession) -> models.User:
    return await crud.create_user(
        db, email="weather-pref@example.com", name="Rider", hashed_password="x"
    )


def _ride(
    *,
    temperature_c=18.0,
    condition="clear",
    precipitation_mm=0.0,
    wind_speed_kph=8.0,
    intensity_factor=0.75,
    duration_seconds=5400,
    outdoor=True,
    date="2026-06-01",
) -> RideWeather:
    return RideWeather(
        date=date,
        outdoor=outdoor,
        temperature_c=temperature_c,
        condition=condition,
        precipitation_mm=precipitation_mm,
        wind_speed_kph=wind_speed_kph,
        duration_seconds=duration_seconds,
        intensity_factor=intensity_factor,
    )


def _mild_baseline(count=8) -> list[RideWeather]:
    return [_ride() for _ in range(count)]


def _find(beliefs, dimension):
    return next((b for b in beliefs if b["dimension"] == dimension), None)


# ---------------------------------------------------------------------------
# Evidence gates: no claim without enough history
# ---------------------------------------------------------------------------


def test_no_beliefs_without_rides():
    assert derive_weather_preferences([]) == []


def test_no_belief_below_the_bucket_gate():
    """Two hot rides is not a pattern, however large the difference looks."""
    rides = [
        *_mild_baseline(),
        _ride(temperature_c=34.0, intensity_factor=0.5),
        _ride(temperature_c=35.0, intensity_factor=0.5),
    ]
    assert _find(derive_weather_preferences(rides), DIMENSION_HEAT) is None


def test_no_belief_without_a_mild_baseline_to_compare_against():
    """Heat rides only: there is nothing to compare them to, so claim nothing."""
    rides = [_ride(temperature_c=33.0) for _ in range(6)]
    assert _find(derive_weather_preferences(rides), DIMENSION_HEAT) is None


def test_inconclusive_effect_produces_no_claim():
    """A small, unremarkable drop sits between the gates — asserting either
    direction would be inventing a pattern."""
    rides = [
        *_mild_baseline(),
        *[_ride(temperature_c=33.0, intensity_factor=0.71) for _ in range(4)],
    ]
    assert _find(derive_weather_preferences(rides), DIMENSION_HEAT) is None


# ---------------------------------------------------------------------------
# Outcome-driven beliefs
# ---------------------------------------------------------------------------


def test_heat_tolerant_athlete_is_not_told_they_slow_in_heat():
    """The headline case: power holds at 33 °C, so the belief is tolerance."""
    rides = [
        *_mild_baseline(),
        *[_ride(temperature_c=33.0, intensity_factor=0.76) for _ in range(5)],
    ]

    belief = _find(derive_weather_preferences(rides), DIMENSION_HEAT)

    assert belief is not None
    assert belief["direction"] == DIRECTION_TOLERANT
    assert belief["statement"] == preference_statement(
        DIMENSION_HEAT, DIRECTION_TOLERANT
    )
    assert belief["evidence"]
    assert belief["alternative_explanations"]
    assert 0.0 < belief["confidence"] <= 0.8


def test_heat_sensitive_athlete_detected_from_intensity_drop():
    rides = [
        *_mild_baseline(),
        *[_ride(temperature_c=33.0, intensity_factor=0.60) for _ in range(5)],
    ]

    belief = _find(derive_weather_preferences(rides), DIMENSION_HEAT)

    assert belief is not None
    assert belief["direction"] == DIRECTION_SENSITIVE


def test_cold_sensitivity_detected_from_shortened_sessions():
    """Intensity holds but the sessions get cut short — still a real signal."""
    rides = [
        *_mild_baseline(),
        *[
            _ride(temperature_c=2.0, intensity_factor=0.75, duration_seconds=2700)
            for _ in range(4)
        ],
    ]

    belief = _find(derive_weather_preferences(rides), DIMENSION_COLD)

    assert belief is not None
    assert belief["direction"] == DIRECTION_SENSITIVE
    assert any("min" in item for item in belief["evidence"])


def test_confidence_grows_with_more_supporting_rides():
    def hot(count):
        return [
            *_mild_baseline(),
            *[_ride(temperature_c=33.0, intensity_factor=0.60) for _ in range(count)],
        ]

    thin = _find(derive_weather_preferences(hot(3)), DIMENSION_HEAT)
    thick = _find(derive_weather_preferences(hot(12)), DIMENSION_HEAT)

    assert thin is not None and thick is not None
    assert thick["confidence"] > thin["confidence"]
    assert thick["confidence"] <= 0.8


# ---------------------------------------------------------------------------
# Behaviour-driven beliefs: rode outside anyway vs moved indoors
# ---------------------------------------------------------------------------


def test_rain_tolerance_from_riding_outside_anyway():
    """No power signal needed: going out in the wet is itself the evidence."""
    rides = [
        *_mild_baseline(),
        *[
            _ride(condition="rain", precipitation_mm=4.0, intensity_factor=0.75)
            for _ in range(6)
        ],
    ]

    belief = _find(derive_weather_preferences(rides), DIMENSION_RAIN)

    assert belief is not None
    assert belief["direction"] == DIRECTION_TOLERANT
    assert any("outdoors" in item for item in belief["evidence"])


def test_rain_aversion_from_moving_indoors():
    rides = [
        *_mild_baseline(),
        *[
            _ride(
                condition="rain",
                precipitation_mm=4.0,
                intensity_factor=0.75,
                outdoor=False,
            )
            for _ in range(6)
        ],
    ]

    belief = _find(derive_weather_preferences(rides), DIMENSION_RAIN)

    assert belief is not None
    assert belief["direction"] == DIRECTION_SENSITIVE
    assert any("indoors" in item for item in belief["evidence"])


def test_contradicting_outcome_and_behaviour_asserts_nothing():
    """Rode out in the rain every time but power collapsed: we do not know."""
    rides = [
        *_mild_baseline(),
        *[
            _ride(condition="rain", precipitation_mm=4.0, intensity_factor=0.55)
            for _ in range(6)
        ],
    ]

    assert _find(derive_weather_preferences(rides), DIMENSION_RAIN) is None


def test_agreeing_outcome_and_behaviour_merge_into_one_belief():
    rides = [
        *_mild_baseline(),
        *[
            _ride(condition="rain", precipitation_mm=4.0, intensity_factor=0.78)
            for _ in range(6)
        ],
    ]

    beliefs = derive_weather_preferences(rides)
    rain = [b for b in beliefs if b["dimension"] == DIMENSION_RAIN]

    assert len(rain) == 1
    assert len(rain[0]["evidence"]) >= 3


# ---------------------------------------------------------------------------
# Projection from stored rides
# ---------------------------------------------------------------------------


def test_ride_weather_prefers_apparent_temperature():
    """Feels-like is what the athlete experienced, so it wins when present."""
    metric = models.RideMetric(
        strava_activity_id=1,
        activity_date="2026-06-01",
        start_lat=47.99,
        start_lng=7.85,
        weather_temperature_c=30.0,
        weather_apparent_temperature_c=36.0,
    )

    projected = ride_weather_from_metric(metric)

    assert projected.temperature_c == 36.0
    assert projected.outdoor is True
    assert projected.is_hot is True


def test_ride_without_gps_is_treated_as_indoor():
    metric = models.RideMetric(
        strava_activity_id=2,
        activity_date="2026-06-01",
        weather_temperature_c=14.0,
        weather_condition="rain",
        weather_source="open_meteo_home",
    )

    projected = ride_weather_from_metric(metric)

    assert projected.outdoor is False
    assert projected.is_wet is True


# ---------------------------------------------------------------------------
# Chat capture
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message,dimension,direction",
    [
        ("I actually love the rain", DIMENSION_RAIN, DIRECTION_TOLERANT),
        ("honestly I don't mind rain at all", DIMENSION_RAIN, DIRECTION_TOLERANT),
        ("I hate riding in the rain", DIMENSION_RAIN, DIRECTION_SENSITIVE),
        ("Ich liebe den Regen", DIMENSION_RAIN, DIRECTION_TOLERANT),
        ("I thrive in the heat", DIMENSION_HEAT, DIRECTION_TOLERANT),
        ("the heat kills me", DIMENSION_HEAT, DIRECTION_SENSITIVE),
        ("the cold doesn't bother me", DIMENSION_COLD, DIRECTION_TOLERANT),
    ],
)
def test_extract_weather_preference_statements(message, dimension, direction):
    found = extract_weather_preference_statements(message)

    assert len(found) == 1
    assert found[0]["dimension"] == dimension
    assert found[0]["direction"] == direction
    assert found[0]["statement"] == preference_statement(dimension, direction)


@pytest.mark.parametrize(
    "message",
    ["", "It was really hot yesterday", "Was the rain forecast right?"],
)
def test_extract_weather_preference_ignores_weather_small_talk(message):
    assert extract_weather_preference_statements(message) == []


def test_extract_weather_preference_keeps_one_direction_per_dimension():
    """A mixed message must not assert both directions for the same condition."""
    found = extract_weather_preference_statements(
        "I love the rain but I hate riding in the rain"
    )
    assert len(found) == 1


# ---------------------------------------------------------------------------
# Persistence lifecycle
# ---------------------------------------------------------------------------


def _persist_rides(db: AsyncSession, user_id: str, rides: list[dict]) -> None:
    for idx, fields in enumerate(rides):
        db.add(
            models.RideMetric(
                user_id=user_id,
                strava_activity_id=1000 + idx,
                activity_date=f"2026-06-{(idx % 28) + 1:02d}",
                duration_seconds=fields.get("duration_seconds", 5400),
                intensity_factor=fields.get("intensity_factor", 0.75),
                start_lat=47.99 if fields.get("outdoor", True) else None,
                start_lng=7.85 if fields.get("outdoor", True) else None,
                weather_temperature_c=fields.get("temperature_c", 18.0),
                weather_condition=fields.get("condition", "clear"),
                weather_precipitation_mm=fields.get("precipitation_mm", 0.0),
                weather_wind_speed_kph=fields.get("wind_speed_kph", 8.0),
            )
        )


@pytest.mark.asyncio
async def test_refresh_persists_beliefs_with_evidence_and_alternatives(
    db: AsyncSession, user: models.User
):
    _persist_rides(
        db,
        user.id,
        [
            *[{} for _ in range(8)],
            *[{"temperature_c": 33.0, "intensity_factor": 0.60} for _ in range(5)],
        ],
    )
    await db.flush()

    count = await refresh_weather_preferences(db, user)

    assert count >= 1
    stored = [
        row
        for row in await crud.list_athlete_hypotheses(db, user.id)
        if row.category == CATEGORY
    ]
    assert stored
    heat = stored[0]
    assert heat.evidence
    assert heat.alternative_explanations
    assert heat.status == "proposed"


@pytest.mark.asyncio
async def test_repeated_refresh_reinforces_without_duplicating(
    db: AsyncSession, user: models.User
):
    _persist_rides(
        db,
        user.id,
        [
            *[{} for _ in range(8)],
            *[{"temperature_c": 33.0, "intensity_factor": 0.60} for _ in range(5)],
        ],
    )
    await db.flush()

    await refresh_weather_preferences(db, user)
    first = [
        (row.statement_key, row.evidence_count, row.confidence)
        for row in await crud.list_athlete_hypotheses(db, user.id)
        if row.category == CATEGORY
    ]
    await refresh_weather_preferences(db, user)
    second = {
        row.statement_key: (row.evidence_count, row.confidence)
        for row in await crud.list_athlete_hypotheses(db, user.id)
        if row.category == CATEGORY
    }

    assert len(first) == len(second)
    for key, evidence_count, confidence in first:
        assert second[key][0] == evidence_count + 1
        assert second[key][1] >= confidence


@pytest.mark.asyncio
async def test_athlete_stated_belief_is_not_decayed_by_absent_ride_data(
    db: AsyncSession, user: models.User
):
    """Absent ride data is not a counter-argument to what the athlete told us."""
    await weather_preference.capture_weather_preferences_from_message(
        db, user.id, "I actually love the rain"
    )
    stated = [
        row
        for row in await crud.list_athlete_hypotheses(db, user.id)
        if row.category == CATEGORY
    ]
    assert len(stated) == 1
    before = stated[0].confidence

    # No rides at all, so the correlation engine supports nothing.
    await refresh_weather_preferences(db, user)

    after = [
        row
        for row in await crud.list_athlete_hypotheses(db, user.id)
        if row.category == CATEGORY
    ]
    assert len(after) == 1
    assert after[0].confidence == pytest.approx(before)


@pytest.mark.asyncio
async def test_unsupported_belief_decays_when_the_data_changes(
    db: AsyncSession, user: models.User
):
    _persist_rides(
        db,
        user.id,
        [
            *[{} for _ in range(8)],
            *[{"temperature_c": 33.0, "intensity_factor": 0.60} for _ in range(5)],
        ],
    )
    await db.flush()
    await refresh_weather_preferences(db, user)
    sensitive_key = next(
        row.statement_key
        for row in await crud.list_athlete_hypotheses(db, user.id)
        if row.category == CATEGORY
    )
    before = next(
        row.confidence
        for row in await crud.list_athlete_hypotheses(db, user.id)
        if row.statement_key == sensitive_key
    )

    # The athlete's hot-weather rides now hold power: the old belief loses support.
    for row in await crud.get_rides_with_weather(db, user.id):
        if row.weather_temperature_c and row.weather_temperature_c >= 29.0:
            row.intensity_factor = 0.78
    await db.flush()
    await refresh_weather_preferences(db, user)

    remaining = {
        row.statement_key: row.confidence
        for row in await crud.list_athlete_hypotheses(db, user.id, include_resolved=True)
    }
    assert sensitive_key not in remaining or remaining[sensitive_key] < before


@pytest.mark.asyncio
async def test_preference_context_reveals_confidence(db: AsyncSession, user: models.User):
    await weather_preference.capture_weather_preferences_from_message(
        db, user.id, "I actually love the rain"
    )

    section = await weather_preference.weather_preference_context_for_user(db, user.id)

    assert "weather tolerances" in section
    assert "confidence" in section
    assert "rain" in section.lower()


@pytest.mark.asyncio
async def test_preference_context_empty_without_beliefs(
    db: AsyncSession, user: models.User
):
    assert await weather_preference.weather_preference_context_for_user(db, user.id) == ""
