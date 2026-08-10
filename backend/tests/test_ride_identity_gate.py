"""Every write path that names a ride goes through one identity gate (#441).

The rule has been written down since #441 and the bug still happened twice more:
the leg-feel PATCH learned it in #442, and #574 added a whole new endpoint that
had never heard of it, so for two weeks every attempt to resolve an ambiguous
intervals ride answered 404 and the card said "Could not save that. Try again in
a moment" — where retrying could never work (PR#588).

A rule that has to be remembered at each new endpoint is a rule that will be
forgotten at one of them. These tests are the version that cannot be.
"""

from __future__ import annotations

import inspect

import pytest

import crud
import schemas
from main import app
from services import ride_matching

# Why the numeric id cannot be the identity, in one line.
CORRUPTING_ID = 7846148097020609552


def test_the_premise():
    """A synthesized 63-bit id does not survive a float64 round trip."""
    assert int(float(CORRUPTING_ID)) != CORRUPTING_ID


# --- The wire ---------------------------------------------------------------


def _ride_request_models() -> list[type]:
    """Every request model that names one of the athlete's activities."""
    models: list[type] = []
    for name, obj in vars(schemas).items():
        if not inspect.isclass(obj) or not issubclass(obj, schemas.BaseModel):
            continue
        if obj is schemas.RideIdentityRequest or name.startswith("_"):
            continue
        fields = set(getattr(obj, "model_fields", {}))
        if "strava_activity_id" not in fields or not name.endswith("Request"):
            continue
        # An explicit opt-out for an id that is not one of our rows — see
        # RateWorkoutRequest, whose id goes straight to Strava's own API.
        if not getattr(obj, "identifies_stored_ride", True):
            continue
        models.append(obj)
    return models


def _exempted_models() -> list[type]:
    return [
        obj
        for name, obj in vars(schemas).items()
        if inspect.isclass(obj)
        and issubclass(obj, schemas.BaseModel)
        and getattr(obj, "identifies_stored_ride", True) is False
    ]


@pytest.mark.parametrize("model", _exempted_models(), ids=lambda m: m.__name__)
def test_an_exemption_says_why(model):
    """An opt-out has to be an argument, not a switch someone flipped."""
    assert (model.__doc__ or "").strip(), (
        f"{model.__name__} opts out of the ride-identity gate without saying why."
    )


def test_there_is_something_to_check():
    """Guard the guard: a rename that empties the sweep must not read as a pass."""
    assert len(_ride_request_models()) >= 1


@pytest.mark.parametrize(
    "model", _ride_request_models(), ids=lambda m: m.__name__
)
def test_a_request_that_names_a_ride_carries_the_string_id(model):
    assert issubclass(model, schemas.RideIdentityRequest), (
        f"{model.__name__} identifies a ride by a number the browser rounds. "
        "Inherit RideIdentityRequest so the provider's string id travels with it."
    )


def test_every_route_keyed_on_a_ride_id_accepts_the_string_id():
    """The other shape: the id in the path rather than in the body."""
    offenders: list[str] = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if "{strava_activity_id}" not in path:
            continue
        endpoint = getattr(route, "endpoint", None)
        if endpoint is None:
            continue
        carries = any(
            inspect.isclass(param.annotation)
            and issubclass(param.annotation, schemas.RideIdentityRequest)
            for param in inspect.signature(endpoint).parameters.values()
            if param.annotation is not inspect.Parameter.empty
        )
        if not carries:
            offenders.append(f"{sorted(getattr(route, 'methods', []) or [])} {path}")

    assert not offenders, (
        "These routes identify a ride by a path parameter the browser rounds: "
        + ", ".join(offenders)
    )


# --- The lookup -------------------------------------------------------------


def test_the_fallback_rule_is_written_once():
    """Three copies of "prefer the external id, else the number" is how they
    drifted. Anything doing it by hand again is a copy that can rot."""
    handwritten = 0
    for module in (crud, ride_matching):
        source = inspect.getsource(module)
        # The definition itself is not a call site.
        handwritten += source.count("get_ride_metric_by_external_id(")
        handwritten -= source.count("async def get_ride_metric_by_external_id(")

    # Once inside the gate itself, and nowhere else.
    assert handwritten == 1, (
        "get_ride_metric_by_external_id is being called outside "
        "crud.get_ride_metric_by_identity — route it through the gate instead."
    )


@pytest.mark.asyncio
async def test_the_gate_prefers_the_string_id(client, auth_headers, mock_ai_service):
    from auth import decode_token
    from tests.conftest import TestSessionLocal

    token = auth_headers["Authorization"].split(" ", 1)[1]
    user_id = decode_token(token)
    async with TestSessionLocal() as db:
        await crud.upsert_ride_metric(
            db,
            user_id,
            strava_activity_id=CORRUPTING_ID,
            activity_source="intervals",
            external_activity_id="i174087702",
            activity_date="2026-08-09",
        )
        await db.commit()

        found = await crud.get_ride_metric_by_identity(
            db, user_id, int(float(CORRUPTING_ID)), "i174087702"
        )
        assert found is not None
        assert found.external_activity_id == "i174087702"


@pytest.mark.asyncio
async def test_the_number_stays_the_fallback(client, auth_headers, mock_ai_service):
    """Strava's own ids are well inside float64, and a client that sends no
    string still has to work."""
    from auth import decode_token
    from tests.conftest import TestSessionLocal

    token = auth_headers["Authorization"].split(" ", 1)[1]
    user_id = decode_token(token)
    async with TestSessionLocal() as db:
        await crud.upsert_ride_metric(
            db, user_id, strava_activity_id=64010, activity_date="2026-05-20"
        )
        await db.commit()

        found = await crud.get_ride_metric_by_identity(db, user_id, 64010, None)
        assert found is not None
        assert found.strava_activity_id == 64010


@pytest.mark.asyncio
async def test_a_string_id_that_matches_nothing_falls_back_rather_than_failing(
    client, auth_headers, mock_ai_service
):
    """A stale client, or a ride re-imported under a new provider id: the number
    is still worth trying before giving up."""
    from auth import decode_token
    from tests.conftest import TestSessionLocal

    token = auth_headers["Authorization"].split(" ", 1)[1]
    user_id = decode_token(token)
    async with TestSessionLocal() as db:
        await crud.upsert_ride_metric(
            db, user_id, strava_activity_id=64011, activity_date="2026-05-21"
        )
        await db.commit()

        found = await crud.get_ride_metric_by_identity(
            db, user_id, 64011, "i-does-not-exist"
        )
        assert found is not None
        assert found.strava_activity_id == 64011
