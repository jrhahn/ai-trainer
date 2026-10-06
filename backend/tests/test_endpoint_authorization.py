"""Every route is authenticated unless it is on the list that says otherwise.

This started as a hunt and turned into a guard, because the hunt found nothing.
The audit behind it: of 116 routes, 106 depend on ``get_current_user`` or
``require_admin``, and the ten that do not are all genuinely public. Identity is
taken from the JWT rather than from anything the client sends — only five routes
accept an id from the path at all, and those pass it to a CRUD call that filters
by owner in the same query, so a foreign id returns ``None`` and the route
answers 404. There is no present hole to find.

Which makes regression the real risk rather than discovery. At the rate this
codebase moves, route 117 arrives next week, and the failure mode is not a
clever attack — it is one forgotten ``Depends``. So this reads the route table
instead of generating traffic: it is total, it runs in milliseconds, and it
cannot be fooled by a case nobody thought to send.

The list below is the whole point. It is not test scaffolding, it is the
inventory of the attack surface, and adding to it has to be a deliberate act
that shows up in a diff.
"""

from __future__ import annotations

import pytest
from fastapi.routing import APIRoute
from httpx import AsyncClient

import auth
from main import app

# Depending on either of these means the route knows who is calling. Everything
# downstream scopes by that identity, so this is the single checkable fact that
# stands between a route and someone else's data.
GUARDS = {auth.get_current_user, auth.require_admin}

# Routes that must stay reachable without a token, each with the reason why.
# A new entry here is a new piece of public attack surface: it belongs in review,
# not in a fixture.
PUBLIC_ROUTES: dict[tuple[str, str], str] = {
    ("POST", "/api/v1/auth/register"): "creating the first account cannot require one",
    ("POST", "/api/v1/auth/login"): "same",
    ("POST", "/api/v1/auth/login/totp"): "second factor, still mid-login",
    ("GET", "/api/v1/auth/session"): "reports whether a session exists; must answer for anonymous callers too",
    ("GET", "/api/v1/auth/captcha/challenge"): "the challenge is what you solve to register (#686)",
    ("GET", "/api/v1/auth/strava/callback"): "Strava redirects the browser here; the caller is Strava, not a session. Its protection is the OAuth state parameter, not a bearer token — see ai-trainer-ops#35",
    ("POST", "/api/v1/admin/login"): "admin login cannot require admin",
    ("GET", "/api/v1/admin/totp-required"): "tells the login form whether to ask for a code, before anyone is logged in",
    ("GET", "/healthz"): "liveness probe, no data",
    ("GET", "/metrics"): "Prometheus exposition. Not routed by Traefik (see the README deployment section), so it is reachable only from the host — if that ever changes it needs a guard, because it leaks user counts and usage shape",
}


def _api_routes(node, prefix: str = ""):
    """Walk the route tree, descending through included routers.

    FastAPI 0.142 keeps an ``include_router`` call as a nested ``_IncludedRouter``
    rather than flattening its routes into ``app.routes``, so a loop over
    ``app.routes`` sees two routes and concludes, wrongly, that the API is
    almost entirely public.
    """
    inner = getattr(node, "original_router", None)
    if inner is not None:
        context = getattr(node, "include_context", None)
        yield from _api_routes(inner, prefix + (getattr(context, "prefix", "") or ""))
        return
    for route in getattr(node, "routes", []):
        if isinstance(route, APIRoute):
            yield prefix + route.path, route
        else:
            yield from _api_routes(route, prefix)


def _dependency_calls(route: APIRoute) -> set:
    """Every callable in the route's dependency tree, at any depth.

    Depth matters: a guard reached through an intermediate dependency counts,
    and a check that only looked at the route's own signature would call such a
    route unprotected.
    """
    found, stack = set(), list(route.dependant.dependencies)
    while stack:
        dependency = stack.pop()
        if dependency.call is not None:
            found.add(dependency.call)
        stack.extend(dependency.dependencies)
    return found


def _route_table() -> list[tuple[str, str, bool]]:
    table = []
    for path, route in _api_routes(app):
        guarded = bool(_dependency_calls(route) & GUARDS)
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            table.append((method, path, guarded))
    return table


def test_the_route_table_was_actually_found():
    """Guard the guard.

    Every assertion below is vacuously true against an empty table, and the walk
    above depends on a FastAPI internal. If an upgrade renames
    ``original_router``, this is the test that says so instead of the suite going
    quietly green on nothing.
    """
    table = _route_table()
    assert len(table) > 100, f"expected the full API, found {len(table)} routes"


def test_every_route_is_authenticated_unless_listed_as_public():
    """The one that will fail the day someone forgets a Depends."""
    unguarded = {(m, p) for m, p, guarded in _route_table() if not guarded}
    unexpected = unguarded - PUBLIC_ROUTES.keys()
    assert not unexpected, (
        "these routes take no identity and are not on the public list:\n"
        + "\n".join(f"  {m} {p}" for m, p in sorted(unexpected))
        + "\n\nAdd `Depends(auth.get_current_user)`, or add an entry to "
        "PUBLIC_ROUTES stating why it may be anonymous."
    )


def test_the_public_list_has_no_stale_entries():
    """Keep the inventory honest in the other direction too.

    An entry that has since been given a guard would otherwise sit here forever,
    describing surface that no longer exists and quietly permitting it to become
    public again.
    """
    unguarded = {(m, p) for m, p, guarded in _route_table() if not guarded}
    stale = PUBLIC_ROUTES.keys() - unguarded
    assert not stale, (
        "these are listed as public but now require authentication — "
        "remove them from PUBLIC_ROUTES:\n"
        + "\n".join(f"  {m} {p}" for m, p in sorted(stale))
    )


# ---------------------------------------------------------------------------
# The five routes that do take an id from the client
# ---------------------------------------------------------------------------
#
# Ownership on these is enforced inside the CRUD query rather than by the
# dependency, so the structural test above cannot see it. Different mechanism,
# so it gets its own check — with traffic, because that is the only way to
# observe it.


async def _register(client: AsyncClient, email: str) -> dict:
    response = await client.post(
        "/api/v1/auth/register",
        json={"name": "Rider", "email": email, "password": "Str0ng!Pass"},
    )
    assert response.status_code in (200, 201), response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.mark.asyncio
async def test_a_race_event_is_invisible_to_another_athlete(client: AsyncClient):
    """B's event id, used with A's token, must not resolve.

    404 rather than 403 is the right answer and the one the code gives: a 403
    would confirm the id exists, which is itself a leak across the boundary.
    """
    alice = await _register(client, "alice@example.com")
    bob = await _register(client, "bob@example.com")

    created = await client.post(
        "/api/v1/users/me/race-events",
        headers=bob,
        json={"date": "2026-08-15", "distanceKm": 120.0, "elevationM": 1800},
    )
    assert created.status_code in (200, 201), created.text
    event_id = created.json()["id"]

    read_back = await client.get("/api/v1/users/me/race-events", headers=bob)
    assert any(e["id"] == event_id for e in read_back.json()["events"])

    update = await client.put(
        f"/api/v1/users/me/race-events/{event_id}",
        headers=alice,
        json={"date": "2026-09-01", "distanceKm": 10.0, "elevationM": 0},
    )
    assert update.status_code == 404, update.text

    delete = await client.delete(f"/api/v1/users/me/race-events/{event_id}", headers=alice)
    assert delete.status_code == 404, delete.text

    # And none of it touched Bob's event.
    still_there = await client.get("/api/v1/users/me/race-events", headers=bob)
    events = still_there.json()["events"]
    assert any(e["id"] == event_id and e["date"] == "2026-08-15" for e in events)


@pytest.mark.asyncio
async def test_an_unauthenticated_caller_gets_401_not_404(client: AsyncClient):
    """Missing a token and missing a resource are different answers.

    Worth pinning because the structural test only proves a guard is *wired*,
    not that it rejects. A guard that resolved to an anonymous user would satisfy
    the route table and fail here.
    """
    response = await client.get("/api/v1/users/me/race-events")
    assert response.status_code in (401, 403), response.text
