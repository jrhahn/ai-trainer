"""What an attacker can learn, and what a borrowed session can destroy (ai-trainer-ops#35).

The issue asked six questions about a surface whose pieces already existed —
``services/captcha.py``, ``services/totp.py``, ``services/rate_limit.py`` — and
whose coverage was good in the places it existed at all. Four of the six were
already answered by a suite: the brute-force limits in
``test_auth_rate_limit.py``, the proof-of-work in ``test_captcha.py``, the
second factor in ``test_totp.py``, revocation in ``test_token_revocation.py``.
This file is the two that nothing answered, plus the structural checks that keep
the answers from drifting.

**Enumeration** was open, and measured open. ``/auth/login`` short-circuited
before the Argon2 verification when the address had no account, so the reply
arrived in 8 ms instead of 165 ms — a twentyfold tell from a single request, no
averaging, no statistics. The same shortcut sat in the Authelia store path, and
there "disabled" was its own fast answer on top.

**Deletion** was open in a way the issue's own last line predicted: *a deletion
after which an issued token still works is not a deletion*. The token was the
half that worked. With header auth on, the password lives in Authelia's
``users_database.yml``, and both deletion routes dropped only the database row;
``POST /auth/login`` recreates a missing row from a valid credential, so the
account came back on the next sign-in with a fresh token. And neither route
asked for the password, so a borrowed unlocked browser was enough to try.

Two questions are answered here by **absence**, which is worth a test rather
than a sentence: there is no password-reset flow and no password-change route.
"The reset flow is the usual giveaway", says the issue — there is nothing to give
anything away yet, and the tests below fail the day that stops being true, which
is the day the enumeration and token-invalidation questions need new answers.
"""

from __future__ import annotations

import ast
import time
from pathlib import Path

import pytest
import yaml
from httpx import AsyncClient

import auth
from config import settings
from routers import dependencies as deps
from services import authelia_store

BACKEND = Path(__file__).resolve().parents[1]

_LOGIN = "/api/v1/auth/login"
_REGISTER = "/api/v1/auth/register"
_ME = "/api/v1/users/me"

_PASSWORD = "Str0ng!Passphrase"
_ATHLETE = "athlete@example.com"
_NOBODY = "nobody-has-this-address@example.com"


def _write_store(path: Path, users: dict | None = None) -> None:
    """A minimal Authelia ``users_database.yml``."""
    path.write_text(yaml.dump({"users": users or {}}), encoding="utf-8")


async def _register(client: AsyncClient, email: str = _ATHLETE) -> str:
    response = await client.post(
        _REGISTER, json={"email": email, "name": "Rider", "password": _PASSWORD}
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


@pytest.fixture
def counted_verifications(monkeypatch) -> list[str]:
    """Record every password verification the request under test performs.

    The point of the timing fix is that the absent-account path spends what the
    present one spends. Asserting that by the clock is an assertion about the
    machine; asserting it by *what was called* is an assertion about the code,
    and it holds on a loaded CI runner. The clock test below exists to show this
    one is counting the thing that actually costs the time.
    """
    calls: list[str] = []
    real = auth.verify_password

    def spy(plain: str, hashed: str) -> bool:
        calls.append(hashed)
        return real(plain, hashed)

    monkeypatch.setattr(auth, "verify_password", spy)
    return calls


@pytest.fixture
def authelia_store_on(monkeypatch, tmp_path) -> Path:
    """Header-auth mode, with a real store file on disk.

    Nothing is mocked: the store is the file the production code reads, so a
    test that pointed a mock at it would only restate its own assumptions.
    """
    store = tmp_path / "users_database.yml"
    _write_store(store)
    monkeypatch.setattr(auth, "AUTHELIA_AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "AUTHELIA_USERS_DB_PATH", str(store))
    return store


# ---------------------------------------------------------------------------
# Account enumeration: does the answer differ when the account exists?
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_login_for_a_missing_account_still_verifies_a_password(
    client: AsyncClient, counted_verifications
):
    """The absent-account path must spend what the wrong-password path spends.

    This is the whole fix, stated as behaviour rather than as a duration: one
    Argon2 verification either way. Before it, ``user is None or not
    verify_password(...)`` skipped the expensive half for an address with no
    account, which is what made the reply fast enough to read as an answer.
    """
    await _register(client)
    counted_verifications.clear()

    await client.post(_LOGIN, json={"email": _ATHLETE, "password": "wrong"})
    for_an_existing_account = len(counted_verifications)
    counted_verifications.clear()

    await client.post(_LOGIN, json={"email": _NOBODY, "password": "wrong"})
    for_a_missing_account = len(counted_verifications)

    assert for_an_existing_account == 1
    assert for_a_missing_account == for_an_existing_account


@pytest.mark.asyncio
async def test_the_stand_in_hash_is_a_real_argon2_hash(client: AsyncClient):
    """A cheap stand-in would reopen the gap while the counting test stayed green.

    The verification has to cost what the real one costs, which means the hash it
    runs against has to carry the same parameters. Built by ``hash_password``
    rather than written down for exactly this reason, and checked here because
    "one verification happened" says nothing about what it cost.
    """
    stand_in = auth._hash_nobody_matches()

    assert stand_in.startswith("$argon2")
    assert not auth.password_needs_rehash(stand_in), (
        "the stand-in hash is out of date against the current Argon2 parameters, "
        "so it is cheaper than the hashes it stands in for"
    )
    assert stand_in == auth._hash_nobody_matches(), "should be computed once"


@pytest.mark.asyncio
async def test_a_missing_account_does_not_answer_faster_than_a_wrong_password(
    client: AsyncClient,
):
    """The empirical anchor: 20x before, about 1x after.

    A ratio and not an absolute, so the machine's speed cancels out, and the
    minimum of several samples rather than the mean, because scheduling noise
    only ever makes a sample slower. The bound is deliberately loose — it is here
    to catch a reopened shortcut, which shows up as an order of magnitude, not as
    a few per cent.
    """
    await _register(client)

    async def fastest(email: str, samples: int = 5) -> float:
        timings = []
        for _ in range(samples):
            started = time.perf_counter()
            response = await client.post(
                _LOGIN, json={"email": email, "password": "wrong"}
            )
            timings.append(time.perf_counter() - started)
            assert response.status_code == 401
        return min(timings)

    existing = await fastest(_ATHLETE)
    missing = await fastest(_NOBODY)

    assert missing > existing * 0.4, (
        f"a login for an address with no account answered in {missing * 1000:.0f} ms "
        f"against {existing * 1000:.0f} ms for one with an account — that ratio is "
        "an enumeration oracle"
    )


@pytest.mark.asyncio
async def test_login_says_the_same_thing_whether_or_not_the_account_exists(
    client: AsyncClient,
):
    """Same status, same words. The timing above was the only tell; keep it so."""
    await _register(client)

    existing = await client.post(_LOGIN, json={"email": _ATHLETE, "password": "wrong"})
    missing = await client.post(_LOGIN, json={"email": _NOBODY, "password": "wrong"})

    assert existing.status_code == missing.status_code == 401
    assert existing.json()["detail"] == missing.json()["detail"]


def test_the_user_store_does_not_answer_faster_for_an_address_it_has_never_seen(
    authelia_store_on: Path, counted_verifications
):
    """Header-auth mode had the same shortcut, plus one of its own.

    Three ways to miss — no entry, an entry marked ``disabled``, an entry with no
    password — and each returned False without hashing anything. The disabled
    case is the interesting one: it told an attacker that the address exists and
    is switched off, which is more than the other two gave away.
    """
    authelia_store.create_user(_ATHLETE, "Rider", _PASSWORD)
    _write_store(
        authelia_store_on.parent / "users_database.yml",
        {
            **yaml.safe_load(authelia_store_on.read_text())["users"],
            "disabled@example.com": {
                "email": "disabled@example.com",
                "disabled": True,
                "password": auth.hash_password(_PASSWORD),
            },
            "nopassword@example.com": {"email": "nopassword@example.com"},
        },
    )

    counts = {}
    for label, email in (
        ("present", _ATHLETE),
        ("absent", _NOBODY),
        ("disabled", "disabled@example.com"),
        ("no password", "nopassword@example.com"),
    ):
        counted_verifications.clear()
        assert authelia_store.verify_credentials(email, "wrong") is False
        counts[label] = len(counted_verifications)

    assert counts == {"present": 1, "absent": 1, "disabled": 1, "no password": 1}, counts


@pytest.mark.asyncio
async def test_registration_does_tell_an_attacker_an_address_is_taken(
    client: AsyncClient,
):
    """Pinned as it is, not fixed: 409 for a taken address, 200 for a free one.

    Hiding it means accepting the registration and sending a "somebody tried to
    register your address" email instead — which needs the SMTP this deployment
    does not have (#687), and tells the *owner* something an attacker can then
    make happen on demand. The bound below is what makes the oracle worth
    keeping rather than worth hiding badly.
    """
    await _register(client)

    taken = await client.post(
        _REGISTER, json={"email": _ATHLETE, "name": "R", "password": _PASSWORD}
    )
    free = await client.post(
        _REGISTER, json={"email": _NOBODY, "name": "R", "password": _PASSWORD}
    )

    assert taken.status_code == 409
    assert free.status_code == 200


@pytest.mark.asyncio
async def test_asking_whether_an_address_is_taken_spends_the_registration_allowance(
    client: AsyncClient, monkeypatch
):
    """Which is what bounds the oracle above to the registration limit.

    ``enforce_registration_rate_limit`` runs before the lookup, so a 409 costs
    the attacker a slot just as a real signup does — five per hour across the
    whole deployment by default. If the limit ever moves behind the existence
    check, enumeration becomes free and this test is what says so.
    """
    await _register(client)
    monkeypatch.setattr(settings, "auth_rate_limit_enabled", True)
    monkeypatch.setattr(settings, "registration_rate_limit_attempts", 2)
    monkeypatch.setattr(settings, "registration_rate_limit_seconds", 3600)
    deps.registration_limiter.reset()

    probes = [
        (
            await client.post(
                _REGISTER,
                json={"email": _ATHLETE, "name": "R", "password": _PASSWORD},
            )
        ).status_code
        for _ in range(3)
    ]
    deps.registration_limiter.reset()

    assert probes == [409, 409, 429], probes


@pytest.mark.asyncio
async def test_there_is_no_reset_flow_to_enumerate_through(client: AsyncClient):
    """The issue calls the reset flow "the usual giveaway". There is no reset flow.

    An athlete who forgets their password currently has no way back in, which is
    a gap in the product (and a launch question of its own, since it needs the
    SMTP of #687) rather than a vulnerability. It is a test because the day
    somebody adds the route, three of this file's answers stop being true at
    once: a reset flow is a new enumeration surface, a new token to get the
    randomness and single-use of right, and a new reason to invalidate issued
    sessions.
    """
    reset_paths = sorted(
        path
        for path in _declared_route_paths()
        if any(word in path for word in ("reset", "forgot", "recover"))
    )
    assert reset_paths == [], (
        f"a credential-recovery route appeared at {reset_paths} — it needs its own "
        "answers for enumeration, token randomness, single use, and session "
        "invalidation (ai-trainer-ops#35)"
    )


# ---------------------------------------------------------------------------
# Deletion: what a borrowed session can do, and what deletion leaves behind
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deleting_the_account_needs_the_password(client: AsyncClient):
    """A live session alone used to be enough to destroy an athlete's history.

    ``/auth/totp/disable`` and ``/auth/sessions/revoke`` both ask, on the
    argument that locking the owner out must cost more than a borrowed unlocked
    browser. This route is that argument's strongest case and was the one that
    did not ask — because ``require_password`` was private to ``auth_router``,
    which ``routers/users.py`` may not import.
    """
    token = await _register(client)
    headers = {"Authorization": f"Bearer {token}"}

    bare = await client.request("DELETE", _ME, headers=headers)
    wrong = await client.request(
        "DELETE", _ME, headers=headers, json={"password": "not-the-password"}
    )

    assert bare.status_code == 422, bare.text
    assert wrong.status_code == 401, wrong.text
    assert (await client.get(_ME, headers=headers)).status_code == 200, (
        "the account survives a refused deletion"
    )


@pytest.mark.asyncio
async def test_a_deleted_account_cannot_be_recreated_by_logging_in_again(
    client: AsyncClient, authelia_store_on: Path
):
    """The bug the issue's last line predicted, in its other half.

    The token did die — ``get_current_user`` loads the row to authenticate, so it
    401s by itself. The *credential* did not: it lives in Authelia's store, the
    deletion dropped only the database row, and ``login`` recreates a missing row
    from a valid credential. Measured before the fix: delete, log in with the
    same password, 200 and a seven-day JWT for a brand-new account bearing the
    same address.
    """
    await client.post(
        _REGISTER, json={"email": _ATHLETE, "name": "Rider", "password": _PASSWORD}
    )
    login = await client.post(_LOGIN, json={"email": _ATHLETE, "password": _PASSWORD})
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    deleted = await client.request(
        "DELETE", _ME, headers=headers, json={"password": _PASSWORD}
    )
    assert deleted.status_code == 200, deleted.text

    again = await client.post(_LOGIN, json={"email": _ATHLETE, "password": _PASSWORD})
    assert again.status_code == 401, (
        "the deleted account's password still opens a session — the credential "
        "outlived the account and login rebuilt the row from it"
    )
    assert yaml.safe_load(authelia_store_on.read_text())["users"] == {}


@pytest.mark.asyncio
async def test_the_token_dies_with_the_account(client: AsyncClient):
    """No generation bump needed: there is no row left to authenticate against."""
    token = await _register(client)
    headers = {"Authorization": f"Bearer {token}"}

    await client.request("DELETE", _ME, headers=headers, json={"password": _PASSWORD})

    assert (await client.get(_ME, headers=headers)).status_code == 401


@pytest.mark.asyncio
async def test_an_operator_deletion_also_takes_the_credential(
    client: AsyncClient, authelia_store_on: Path, monkeypatch
):
    """Same gap on the admin route, which is the one used on an abusive account.

    Two routes delete a user and neither could reach the store, which is the
    whole reason ``services/authelia_store.py`` exists. Fixing only the
    self-service half would have left the operator telling someone they are gone
    while their password still worked.
    """
    monkeypatch.setattr(settings, "admin_password", "admin-pw-for-this-test")
    await client.post(
        _REGISTER, json={"email": _ATHLETE, "name": "Rider", "password": _PASSWORD}
    )
    login = await client.post(_LOGIN, json={"email": _ATHLETE, "password": _PASSWORD})
    user_id = auth.read_access_token(login.json()["access_token"]).user_id

    admin = await client.post(
        "/api/v1/admin/login", json={"password": "admin-pw-for-this-test"}
    )
    assert admin.status_code == 200, admin.text
    deleted = await client.delete(
        f"/api/v1/admin/users/{user_id}",
        headers={"Authorization": f"Bearer {admin.json()['access_token']}"},
    )

    assert deleted.status_code == 204, deleted.text
    assert yaml.safe_load(authelia_store_on.read_text())["users"] == {}
    assert (
        await client.post(_LOGIN, json={"email": _ATHLETE, "password": _PASSWORD})
    ).status_code == 401


@pytest.mark.asyncio
async def test_an_operator_deletion_refuses_when_the_store_is_not_there(
    client: AsyncClient, monkeypatch, tmp_path
):
    """A missing store is refused, where a missing *entry* is a no-op.

    The difference is whether the absence is trustworthy. An account with no
    entry in a store this process can read has demonstrably no credential. A
    store that is not there at all has demonstrated nothing: on this deployment
    the file is a bind mount, and a mount that failed to come up looks exactly
    like a file that was never written — while the real one, with the real
    password hashes, sits intact on the host. Deleting the row then is the bug
    this suite exists for, with the credential surviving on a volume nobody
    noticed was detached.

    So it fails, and only the operator route can reach it: self-service deletion
    checks the password first, and in header-auth mode that check reads the same
    missing store and 401s before this is asked.
    """
    monkeypatch.setattr(settings, "admin_password", "admin-pw-for-this-test")
    token = await _register(client)
    user_id = auth.read_access_token(token).user_id

    monkeypatch.setattr(auth, "AUTHELIA_AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "AUTHELIA_USERS_DB_PATH", str(tmp_path / "never.yml"))

    admin = await client.post(
        "/api/v1/admin/login", json={"password": "admin-pw-for-this-test"}
    )
    refused = await client.delete(
        f"/api/v1/admin/users/{user_id}",
        headers={"Authorization": f"Bearer {admin.json()['access_token']}"},
    )

    assert refused.status_code == 503, refused.text

    # Read back outside header-auth mode, so the check that proves the row
    # survived is not the same missing store that caused the 503.
    monkeypatch.setattr(auth, "AUTHELIA_AUTH_ENABLED", False)
    still_there = await client.get(_ME, headers={"Authorization": f"Bearer {token}"})
    assert still_there.status_code == 200, (
        "the account is gone after a refused deletion — the 503 promised the "
        "opposite, and an operator would have stopped looking"
    )


@pytest.mark.asyncio
async def test_a_store_it_cannot_write_fails_the_deletion_rather_than_faking_it(
    client: AsyncClient, authelia_store_on: Path, monkeypatch
):
    """The one outcome worse than a failed deletion is a fake one.

    The store is a bind mount shared with the Authelia container, whose
    entrypoint has twice chowned it away from this process (#684, #696). If that
    happens mid-deletion, the athlete must not be told their account is gone —
    they would stop looking, and their password would still work.
    """
    await client.post(
        _REGISTER, json={"email": _ATHLETE, "name": "Rider", "password": _PASSWORD}
    )
    login = await client.post(_LOGIN, json={"email": _ATHLETE, "password": _PASSWORD})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    def _denied(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("services.authelia_store._write_atomically", _denied)

    refused = await client.request(
        "DELETE", _ME, headers=headers, json={"password": _PASSWORD}
    )

    assert refused.status_code == 503, refused.text
    assert (await client.get(_ME, headers=headers)).status_code == 200, (
        "the account is still there, which is what the 503 promised"
    )


@pytest.mark.asyncio
async def test_a_store_that_no_longer_parses_is_an_operator_problem_everywhere(
    client: AsyncClient, authelia_store_on: Path
):
    """Invalid YAML is the same condition as an unopenable file, and likelier.

    Editing ``users_database.yml`` by hand is how a user gets added and the only
    way a forgotten password gets reset, since neither has a route — so a half
    saved file is a realistic state, and it used to be a bare 500 with the
    traceback as its only explanation. Raised from one place in the service, so
    login, registration and deletion all report it the same way rather than two
    of them reporting it and the third inheriting a 500.
    """
    await client.post(
        _REGISTER, json={"email": _ATHLETE, "name": "Rider", "password": _PASSWORD}
    )
    login = await client.post(_LOGIN, json={"email": _ATHLETE, "password": _PASSWORD})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    authelia_store_on.write_text("users:\n  rider:\n  bad: [unclosed\n")

    deleted = await client.request(
        "DELETE", _ME, headers=headers, json={"password": _PASSWORD}
    )
    signed_in = await client.post(
        _LOGIN, json={"email": _ATHLETE, "password": _PASSWORD}
    )
    signed_up = await client.post(
        _REGISTER, json={"email": "new@example.com", "name": "N", "password": _PASSWORD}
    )

    assert [deleted.status_code, signed_in.status_code, signed_up.status_code] == [
        503,
        503,
        503,
    ], (deleted.text, signed_in.text, signed_up.text)
    assert signed_in.status_code != 401, (
        "a file that does not parse is not a wrong password, and saying so sends "
        "the operator looking at the wrong thing"
    )


def test_removing_an_entry_the_store_never_held_is_not_an_error(
    authelia_store_on: Path,
):
    """A missing entry is a no-op, so the deletion routes can call unconditionally.

    Otherwise each route would have to decide for itself whether this deployment
    has a store and whether this account is in it — and that is the shape in
    which one of them ends up not calling at all, which is the bug being fixed.

    Separately: an account that predates the switch to header auth has no entry
    here and cannot log in either, because nothing consults its
    ``hashed_password`` column in that mode. That is a pre-existing lockout, not
    something deletion introduces, and it belongs with #14.
    """
    authelia_store.create_user(_ATHLETE, "Rider", _PASSWORD)

    assert authelia_store.delete_user("never-registered@example.com") is False
    assert authelia_store.delete_user(_ATHLETE) is True
    assert yaml.safe_load(authelia_store_on.read_text())["users"] == {}


def test_removing_an_entry_does_nothing_when_header_auth_is_off(monkeypatch) -> None:
    """With no store configured there is nothing to remove and no error to raise.

    This is the branch every non-Authelia deployment takes, including the one in
    production today — which is why ``delete_user`` has to be callable without
    the caller first asking whether it applies.
    """
    monkeypatch.setattr(auth, "AUTHELIA_AUTH_ENABLED", False)
    monkeypatch.setattr(auth, "AUTHELIA_USERS_DB_PATH", "/nonexistent/users.yml")

    assert authelia_store.delete_user(_ATHLETE) is False


# ---------------------------------------------------------------------------
# Structural: the answers above, kept from drifting
# ---------------------------------------------------------------------------


def _routes_in(module: str) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    """Every route handler in *module*, by function name."""
    tree = ast.parse((BACKEND / "routers" / f"{module}.py").read_text())
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            isinstance(dec, ast.Call)
            and isinstance(dec.func, ast.Attribute)
            and isinstance(dec.func.value, ast.Name)
            and dec.func.value.id == "router"
            for dec in node.decorator_list
        )
    }


def _calls_in(node: ast.AST) -> set[str]:
    return {
        call.func.id
        for call in ast.walk(node)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
    }


def _declared_route_paths() -> set[str]:
    """Every path literal any router declares, prefix included.

    Read from the source rather than from ``app.routes``, which was the first
    attempt and was silently vacuous: FastAPI wraps an included router in a lazy
    ``_IncludedRouter`` node, so walking ``app.routes`` finds ``/healthz``,
    ``/docs`` and nothing else. A prohibition that enumerates an empty list
    passes forever — which is exactly what a mutation test is for.

    Reading the source also sees a route declared with
    ``include_in_schema=False``, which an OpenAPI-based sweep would not.
    """
    paths: set[str] = set()
    for module in sorted((BACKEND / "routers").glob("*.py")):
        tree = ast.parse(module.read_text())
        prefixes = [
            keyword.value.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "APIRouter"
            for keyword in node.keywords
            if keyword.arg == "prefix" and isinstance(keyword.value, ast.Constant)
        ]
        prefix = prefixes[0] if prefixes else ""
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if not (
                isinstance(node.func.value, ast.Name)
                and node.func.value.id == "router"
                and node.func.attr in {"get", "post", "put", "patch", "delete"}
            ):
                continue
            if node.args and isinstance(node.args[0], ast.Constant):
                paths.add(prefix + node.args[0].value)
    return paths


def test_the_route_inventory_is_not_empty() -> None:
    """Every sweep below is vacuous if the parse finds nothing.

    Named routes rather than a count, because a count is satisfied by finding
    the wrong things.
    """
    assert len(_routes_in("auth_router")) >= 10
    assert "delete_me" in _routes_in("users")

    declared = _declared_route_paths()
    for expected in ("/auth/login", "/auth/register", "/auth/sessions/revoke"):
        assert expected in declared, f"{expected} not found — the sweep is blind"


def test_every_route_that_takes_a_secret_spends_a_rate_limit_allowance() -> None:
    """A password or a code arriving at an unbounded route is a brute-force door.

    Checked by reading the handlers rather than by hitting them, because the
    failure mode is a *new* route: ``test_auth_rate_limit.py`` exercises the
    limits that exist, and nothing noticed a route that never asked for one.
    ``/admin/login`` was exactly that until #682.

    Read by AST so that a mention in a comment cannot stand in for a call.
    """
    guards = {
        "enforce_login_rate_limit",
        "enforce_registration_rate_limit",
        "enforce_totp_code_rate_limit",
        "enforce_admin_login_rate_limit",
        "enforce_admin_totp_rate_limit",
        "enforce_captcha_rate_limit",
    }
    # Routes whose body carries a password or a one-time code, so a guess at it
    # is worth making. ``totp_enroll`` is not one: it accepts no secret, and its
    # own 409 stops a borrowed session replacing a live enrollment.
    takes_a_secret = {
        "register",
        "login",
        "login_totp",
        "totp_confirm",
        "totp_disable",
        "revoke_sessions",
    }

    handlers = _routes_in("auth_router")
    missing = [
        name
        for name in sorted(takes_a_secret)
        if not (_calls_in(handlers[name]) & guards)
    ]
    assert missing == [], f"these routes accept a secret at unbounded rate: {missing}"

    assert _calls_in(_routes_in("users")["delete_me"]) & guards, (
        "DELETE /users/me now takes a password, so it is a guessing surface too"
    )


def test_no_auth_limit_is_keyed_on_the_client_address() -> None:
    """Answering the issue's "what happens behind shared NAT" — nothing does.

    Deliberate, and documented in ``routers/dependencies.py``: the client address
    arrives through Traefik *and* nginx, so trusting it needs a trusted-proxy hop
    count nothing in this app establishes. A limit keyed on a header an attacker
    can set is worse than no limit, because it reads as one.

    The cost is accepted on both sides: a shared NAT cannot lock its neighbours
    out of an account that is not theirs, and an attacker with many addresses
    gets no more allowance for them. The per-email and global windows are what
    bound both.
    """
    source = (BACKEND / "routers" / "dependencies.py").read_text()
    tree = ast.parse(source)
    limit_functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name.startswith("enforce_")
    ]
    assert limit_functions, "no limit functions found — this sweep would be vacuous"

    for node in limit_functions:
        text = ast.unparse(node)
        for forbidden in ("client.host", "x-forwarded-for", "X-Forwarded-For", "x-real-ip"):
            assert forbidden not in text, (
                f"{node.name} keys on the client address ({forbidden}), which "
                "arrives through two proxies and is not established as trustworthy"
            )


def test_there_is_no_way_to_change_a_password_without_invalidating_sessions() -> None:
    """The issue asks what invalidates a JWT on a password change. Nothing does.

    Because there is no password change: no route, no store write, no column
    update outside registration and the rehash-on-successful-login of #329, which
    does not change the password. So the question has no answer to be wrong
    about — and this test is here so that adding the route forces someone to give
    it one, next to ``crud.revoke_user_tokens``, which already exists for it.
    """
    change_paths = sorted(
        path for path in _declared_route_paths() if "password" in path
    )
    assert change_paths == [], (
        f"a password route appeared at {change_paths} — it has to bump "
        "token_generation, or every session issued under the old password "
        "outlives it by up to seven days (ai-trainer-ops#35)"
    )


@pytest.mark.asyncio
async def test_revoking_sessions_is_what_invalidates_a_token_today(
    client: AsyncClient,
):
    """Named here because it is the answer to the issue's last question.

    The detail is checked in ``test_token_revocation.py``; this is the one line
    that says *which* lever exists, so the catalogue in this file is complete
    rather than silently missing the one control that works.
    """
    token = await _register(client)
    headers = {"Authorization": f"Bearer {token}"}

    revoked = await client.post(
        "/api/v1/auth/sessions/revoke", headers=headers, json={"password": _PASSWORD}
    )

    assert revoked.status_code == 200, revoked.text
    assert (await client.get(_ME, headers=headers)).status_code == 401
