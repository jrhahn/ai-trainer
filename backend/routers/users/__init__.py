"""The athlete's own routes under /users/me, one module per concern (ai-trainer-ops#251).

Each module holds its routes on a router of its own, with the prefix and the tag
they always had. ``router`` includes them all, and main.py includes ``router``.
None of the routes can match another one's path, so the order below changes
nothing about which route answers a request; tests/test_users_routes_251.py
holds that.
"""

from fastapi import APIRouter

from . import (
    ai_key,
    chat,
    coach_timeline,
    fit,
    location,
    memory,
    metrics,
    plan,
    profile,
    races,
    strength,
    workouts,
)

router = APIRouter()
for _module in (
    profile,
    plan,
    workouts,
    strength,
    races,
    chat,
    memory,
    coach_timeline,
    ai_key,
    metrics,
    location,
    fit,
):
    router.include_router(_module.router)
