"""Breaking the ``Remote-*`` identity boundary instead of reading about it.

``README.md`` describes this boundary at length and calls it belt-and-suspenders
outright: the backend is not behind Authelia as forward auth, Traefik strips
inbound ``Remote-User``/``Remote-Email``/``Remote-Name``, the backend port binds
to loopback, and ``Remote-*`` is ignored unless a request carries
``AUTHELIA_PROXY_SHARED_SECRET``. Carefully thought through — which is why
ai-trainer-ops#34 asked for it to be attacked rather than re-read.

What the attack found
---------------------
The last clause was false, and it was the load-bearing one. The shared secret is
``optional`` in ``deploy/forwarded-vars.yml`` and empty on this deployment, and
an empty secret used to mean *trust the headers*, not *ignore them*. A forged
``Remote-Email`` with no bearer token at all returned 200 from
``/api/v1/users/me`` for another athlete's account, and ``/api/v1/auth/session``
minted a seven-day JWT for it — see
:func:`test_a_forged_header_cannot_authenticate_as_another_athlete`, which is
that request, now expecting 401.

So the belt was never fastened and the suspenders were two hand-maintained
header lists in two different proxies. ``auth._request_from_trusted_proxy`` now
fails closed, which makes the backend's own check the boundary; the strip lists
go back to being the defence in depth they were always described as.

Three layers, because one is not an argument
--------------------------------------------
No test here can see Traefik — it is not in this test environment, and a test
that mocks the proxy it is trying to break would only restate its own
assumptions. The boundary is argued in three measurable parts instead, and the
parts have to meet:

1. **The wire** — a real uvicorn, driven with hand-written bytes, decides which
   header shapes can arrive at all and what they look like once they do.
   Everything about spelling and smuggling is decided here.
2. **The app** — the real ASGI app decides what it does with each shape that can
   arrive, including which spellings it honours as identity.
3. **The deployment files** — ``compose.yml`` and ``frontend/nginx.conf`` must
   delete exactly the header names layer 2 honours, on every route that reaches
   the backend.

Layer 1 closing the alphabet is what makes layer 3 finite: uvicorn lower-cases
every header name before the app sees it, so "the names the app honours" is a
set of three strings rather than an open-ended family of spellings, and a strip
list can be checked against it.
"""

from __future__ import annotations

import json
import re
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn

import auth
import config

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "compose.yml"
NGINX_CONF = REPO_ROOT / "frontend" / "nginx.conf"

PROXY_SECRET = "proxy-shared-secret-for-tests"
SECRET_HEADER = "X-Authelia-Proxy-Secret"
ATHLETE = "athlete@example.com"
INTRUDER = "intruder@example.com"


# ---------------------------------------------------------------------------
# Layer 1: the wire. What can arrive, and in what shape?
# ---------------------------------------------------------------------------
#
# A real uvicorn in a thread, driven with raw bytes rather than a client
# library. The point is precisely to send what no client library will: a space
# before the colon, an obs-fold continuation, a bare LF terminator. httpx cannot
# express those, and ``ASGITransport`` would not parse them if it could — it
# hands the app a header list the test built, which answers nothing about what a
# header parser accepts.
#
# The app under uvicorn here is a four-line echo, not the real one. What is
# being measured is header parsing, and booting the real app would add a database
# and a scheduler to a question neither one takes part in.
#
# The parser is httptools: ``uvicorn[standard]==0.54.0`` is a pinned dependency
# and uvicorn's ``auto`` http setting prefers it whenever it imports. Worth
# knowing because the measurements below are of that parser — h11 is stricter in
# places, so dropping the extra would change answers here rather than silently
# agree with them.

_RECEIVED: list[list[tuple[str, str]]] = []


async def _echo(scope, receive, send) -> None:
    """Record the headers the ASGI layer was handed, and say so."""
    if scope["type"] != "http":  # pragma: no cover - lifespan only
        return
    headers = [(k.decode("latin-1"), v.decode("latin-1")) for k, v in scope["headers"]]
    _RECEIVED.append(headers)
    body = json.dumps(headers).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


@pytest.fixture(scope="module")
def wire() -> object:
    """A live uvicorn serving :func:`_echo`, with a raw-bytes sender."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    server = uvicorn.Server(
        uvicorn.Config(_echo, host="127.0.0.1", port=port, log_level="critical")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started, "uvicorn did not come up; the wire layer measures nothing"

    class Wire:
        @staticmethod
        def send(header_block: bytes, *, timeout: float = 2.0):
            """Return (status line, headers the app saw) for one raw request."""
            request = b"GET / HTTP/1.1\r\nHost: testserver\r\n" + header_block + b"\r\n"
            before = len(_RECEIVED)
            with socket.create_connection(("127.0.0.1", port), timeout=timeout) as sock:
                sock.sendall(request)
                chunks = []
                try:
                    while True:
                        chunk = sock.recv(65536)
                        if not chunk:
                            break
                        chunks.append(chunk)
                except socket.timeout:  # pragma: no cover - server always answers
                    pass
            response = b"".join(chunks)
            status = response.split(b"\r\n", 1)[0].decode("latin-1", "replace")
            arrived = _RECEIVED[before] if len(_RECEIVED) > before else None
            return status, arrived

    try:
        yield Wire()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_the_wire_fixture_actually_reaches_the_app(wire) -> None:
    """The companion assertion for every rejection measured below.

    A fixture that failed to start would make each "the server rejects this"
    test pass for the wrong reason, so one case has to arrive.
    """
    status, arrived = wire.send(b"Remote-Email: " + ATHLETE.encode() + b"\r\n")
    assert status == "HTTP/1.1 200 OK"
    assert ("remote-email", ATHLETE) in arrived


@pytest.mark.parametrize(
    "spelling",
    [
        b"Remote-Email",
        b"remote-email",
        b"REMOTE-EMAIL",
        b"ReMoTe-EmAiL",
    ],
)
def test_every_case_of_a_header_name_arrives_lower_cased(wire, spelling) -> None:
    """Case is not a variant, it is the same header.

    This is what bounds layer 3. If the app can only ever be handed
    ``remote-email``, then one entry in a strip list covers every way a client
    can type it — and the strip lists in both proxies match case-insensitively
    for the same reason.
    """
    status, arrived = wire.send(spelling + b": " + ATHLETE.encode() + b"\r\n")
    assert status == "HTTP/1.1 200 OK"
    names = [name for name, _ in arrived]
    assert "remote-email" in names
    assert names == [name.lower() for name in names]


def test_an_underscore_is_a_different_header_and_arrives_as_one(wire) -> None:
    """``Remote_Email`` is not a spelling of ``Remote-Email``.

    httptools passes the underscore through verbatim, so the name stays
    distinct — which is why neither proxy strips it, and why
    :func:`test_no_near_miss_spelling_authenticates` has to show the app ignores
    it. Gateways exist that fold ``_`` to ``-``; putting one in front of this
    app would make the underscore a bypass, and these two tests are what would
    notice.
    """
    status, arrived = wire.send(b"Remote_Email: " + ATHLETE.encode() + b"\r\n")
    assert status == "HTTP/1.1 200 OK"
    assert ("remote_email", ATHLETE) in arrived
    assert "remote-email" not in [name for name, _ in arrived]


def test_leading_whitespace_in_a_value_is_eaten_before_the_app_sees_it(wire) -> None:
    status, arrived = wire.send(b"Remote-Email:  \t  " + ATHLETE.encode() + b"\r\n")
    assert status == "HTTP/1.1 200 OK"
    assert ("remote-email", ATHLETE) in arrived


def test_trailing_whitespace_in_a_value_survives_to_the_app(wire) -> None:
    """Measured, and the asymmetry with leading whitespace is the point.

    httptools strips the optional whitespace after the colon and keeps what
    follows the value, so the app is handed ``"athlete@example.com   "``. Which
    makes it the app's problem whether that is a different athlete — see
    :func:`test_whitespace_around_an_address_is_not_a_second_athlete`.
    """
    status, arrived = wire.send(b"Remote-Email: " + ATHLETE.encode() + b"   \r\n")
    assert status == "HTTP/1.1 200 OK"
    assert ("remote-email", ATHLETE + "   ") in arrived


@pytest.mark.parametrize(
    ("attack", "header_block"),
    [
        # RFC 9110 forbids whitespace before the colon precisely because two
        # proxies in a chain may disagree about whether this is a header at all.
        ("space before the colon", b"Remote-Email : " + ATHLETE.encode() + b"\r\n"),
        # obs-fold: a continuation line that a strict parser and a lenient one
        # read as different values.
        (
            "obs-fold continuation",
            b"Remote-Email: nobody@example.com\r\n\t" + ATHLETE.encode() + b"\r\n",
        ),
        # A bare LF where CRLF is required is the classic desync: the front
        # parser ends the header, the back one does not, or the reverse.
        ("bare LF terminator", b"Remote-Email: " + ATHLETE.encode() + b"\n"),
        ("NUL in the value", b"Remote-Email: " + ATHLETE.encode() + b"\x00\r\n"),
        ("CR in the value", b"Remote-Email: " + ATHLETE.encode() + b"\rX\r\n"),
        # Both framings at once — whichever one a proxy believes, the other
        # body boundary is somewhere else.
        ("Content-Length and Transfer-Encoding", b"Transfer-Encoding: chunked\r\nContent-Length: 0\r\n"),
        ("two disagreeing Content-Lengths", b"Content-Length: 0\r\nContent-Length: 6\r\n"),
    ],
)
def test_the_server_refuses_the_shapes_a_proxy_pair_could_disagree_about(
    wire, attack, header_block
) -> None:
    """Request smuggling, as far as one end of the pair can answer it.

    Each of these is a shape whose meaning depends on which parser is reading,
    which is the whole mechanism of smuggling a header past a proxy that would
    have stripped it. uvicorn answers 400 and never invokes the app, so the
    desync has no second half here regardless of how Traefik reads it.

    What this does *not* establish is the Traefik side: a front proxy that
    normalises one of these into a well-formed ``Remote-Email`` would hand
    uvicorn something clean. That needs the real pair, and it is the part of
    ai-trainer-ops#34 that stays open.
    """
    status, arrived = wire.send(header_block)
    assert status.startswith("HTTP/1.1 400"), f"{attack} was accepted: {status}"
    assert arrived is None, f"{attack} reached the application"


def test_duplicate_headers_arrive_as_two_entries_rather_than_one_joined_value(
    wire,
) -> None:
    """Neither folded nor deduplicated — so the app chooses, and layer 2 pins it.

    Traefik's ``customRequestHeaders`` with an empty value deletes every
    occurrence, so a duplicate is not expected to survive the proxy at all. This
    measures what happens if one does.
    """
    status, arrived = wire.send(
        b"Remote-Email: " + INTRUDER.encode() + b"\r\n"
        b"Remote-Email: " + ATHLETE.encode() + b"\r\n"
    )
    assert status == "HTTP/1.1 200 OK"
    assert [value for name, value in arrived if name == "remote-email"] == [
        INTRUDER,
        ATHLETE,
    ]


# ---------------------------------------------------------------------------
# Layer 2: the app. What does it do with a shape that arrived?
# ---------------------------------------------------------------------------


@pytest.fixture
def authelia_mode(monkeypatch):
    """Authelia header auth, configured the way a forward-auth deployment would.

    Registration is unavailable in this mode (it wants the Authelia user store),
    so a test that needs an existing athlete has to create one before switching
    it on — which is what :func:`athlete` does.
    """
    monkeypatch.setattr(auth, "AUTHELIA_AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "AUTHELIA_PROXY_SHARED_SECRET", PROXY_SECRET)
    monkeypatch.setattr(auth, "AUTHELIA_PROXY_SECRET_HEADER", SECRET_HEADER)


@pytest.fixture
def no_proxy_secret(monkeypatch):
    """Authelia header auth with the secret left empty — this deployment's state.

    ``authelia_proxy_shared_secret`` is ``optional`` in
    ``deploy/forwarded-vars.yml``, so production runs exactly here.
    """
    monkeypatch.setattr(auth, "AUTHELIA_AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "AUTHELIA_PROXY_SHARED_SECRET", "")
    monkeypatch.setattr(auth, "AUTHELIA_PROXY_SECRET_HEADER", SECRET_HEADER)


@pytest.fixture
async def athlete(client):
    """A registered athlete, created before Authelia mode is switched on."""
    response = await client.post(
        "/api/v1/auth/register",
        json={"name": "Test Rider", "email": ATHLETE, "password": "Str0ng!Pass"},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


@pytest.mark.asyncio
async def test_a_forged_header_cannot_authenticate_as_another_athlete(
    client, athlete, no_proxy_secret
) -> None:
    """The request that worked, with the configuration that production runs.

    No bearer token, one header, somebody else's address. This returned 200 and
    the athlete's full profile — email, name, Strava connection state — until
    ``_request_from_trusted_proxy`` started failing closed.
    """
    response = await client.get("/api/v1/users/me", headers={"Remote-Email": ATHLETE})
    assert response.status_code == 401, response.text


@pytest.mark.asyncio
async def test_a_forged_header_cannot_mint_a_session_token(
    client, athlete, no_proxy_secret
) -> None:
    """``/auth/session`` is the sharper half: it hands back a usable JWT.

    A token from here outlives the forged header by seven days and is
    indistinguishable from a password login afterwards, which is what made this
    worse than the per-request impersonation above.
    """
    response = await client.get("/api/v1/auth/session", headers={"Remote-Email": ATHLETE})
    assert response.status_code == 401, response.text
    assert "access_token" not in response.text


@pytest.mark.asyncio
async def test_a_forged_header_cannot_create_an_account(
    client, no_proxy_secret
) -> None:
    """Impersonation needed an existing athlete; this needs nobody at all.

    The header path creates the account it cannot find, so a forged header was
    also a registration endpoint that no rate limit, captcha or password policy
    was watching.
    """
    response = await client.get("/api/v1/users/me", headers={"Remote-Email": INTRUDER})
    assert response.status_code == 401, response.text


@pytest.mark.asyncio
async def test_the_boot_log_says_header_auth_is_off_rather_than_unprotected(
    no_proxy_secret, caplog
) -> None:
    """The warning has to describe the new answer, not the old one.

    It used to say ``Remote-*`` headers were trusted on any request — true then,
    and exactly backwards now. An operator who reads the old wording would go
    looking for an exposure instead of for the missing secret.
    """
    with caplog.at_level("WARNING"):
        auth.warn_if_authelia_header_auth_is_off()
    assert "are ignored on every request" in caplog.text
    assert "trusted on any request" not in caplog.text


@pytest.mark.asyncio
async def test_the_proxy_secret_is_what_makes_a_header_believed(
    client, athlete, authelia_mode
) -> None:
    """The other side of failing closed: with the secret, the path still works.

    Without this the whole mechanism could be switched off and every test above
    would still pass.
    """
    response = await client.get(
        "/api/v1/users/me",
        headers={"Remote-Email": ATHLETE, SECRET_HEADER: PROXY_SECRET},
    )
    assert response.status_code == 200, response.text
    assert response.json()["email"] == ATHLETE


@pytest.mark.parametrize(
    ("attempt", "value"),
    [
        ("absent", None),
        ("empty", ""),
        ("one character short", PROXY_SECRET[:-1]),
        ("one character long", PROXY_SECRET + "x"),
        ("the right length, wrong bytes", "x" * len(PROXY_SECRET)),
        ("a prefix", PROXY_SECRET[:8]),
        ("whitespace-padded", f" {PROXY_SECRET} "),
    ],
)
@pytest.mark.asyncio
async def test_no_near_miss_of_the_proxy_secret_is_accepted(
    client, athlete, authelia_mode, attempt, value
) -> None:
    headers = {"Remote-Email": ATHLETE}
    if value is not None:
        headers[SECRET_HEADER] = value
    response = await client.get("/api/v1/users/me", headers=headers)
    assert response.status_code == 401, f"{attempt} was accepted"


@pytest.mark.asyncio
async def test_a_client_cannot_prepend_its_own_proxy_secret_header(
    client, athlete, authelia_mode
) -> None:
    """A duplicate secret header resolves to the first one, which fails closed.

    Worth pinning in this direction specifically: the proxies set this header by
    overwrite, so if a client's copy ever survived alongside the real one, it
    arriving first is the harmless ordering.
    """
    response = await client.get(
        "/api/v1/users/me",
        headers=[
            ("Remote-Email", ATHLETE),
            (SECRET_HEADER, "forged-by-the-client"),
            (SECRET_HEADER, PROXY_SECRET),
        ],
    )
    assert response.status_code == 401, response.text


@pytest.mark.parametrize(
    "spelling",
    ["Remote-Email", "remote-email", "REMOTE-EMAIL", "ReMoTe-EmAiL"],
)
@pytest.mark.asyncio
async def test_the_app_honours_every_case_of_the_configured_header_name(
    client, athlete, authelia_mode, spelling
) -> None:
    """The measurement layer 3 depends on, in the direction that matters.

    These all authenticate, so all of them must be stripped — which they are,
    because both proxies delete header names case-insensitively and layer 1
    showed the app can only ever be handed the lower-cased form.
    """
    response = await client.get(
        "/api/v1/users/me",
        headers={spelling: ATHLETE, SECRET_HEADER: PROXY_SECRET},
    )
    assert response.status_code == 200, response.text


@pytest.mark.parametrize(
    "spelling",
    [
        "Remote_Email",
        "Remote-Email.",
        "X-Remote-Email",
        "X-Forwarded-Email",
        "X-Forwarded-User",
        "Remote-Emails",
        "RemoteEmail",
        "Remote-E-mail",
    ],
)
@pytest.mark.asyncio
async def test_no_near_miss_spelling_authenticates(
    client, athlete, authelia_mode, spelling
) -> None:
    """Every name a proxy does *not* strip has to be a name the app ignores.

    That is the closed half of the argument: the strip lists cover three names,
    so anything outside them — underscores, ``X-`` prefixes, a trailing dot —
    must be inert at the backend, and this is what keeps it so. ``X-Forwarded-*``
    is in the list because nginx sets those itself and the app must not mistake
    them for identity.
    """
    response = await client.get(
        "/api/v1/users/me",
        headers={spelling: ATHLETE, SECRET_HEADER: PROXY_SECRET},
    )
    assert response.status_code == 401, f"{spelling} authenticated"


@pytest.mark.asyncio
async def test_remote_user_alone_does_not_authenticate(
    client, athlete, authelia_mode
) -> None:
    """Identity hangs on ``Remote-Email``; the other two only name the account.

    Which is worth knowing when reading the strip list: ``Remote-User`` and
    ``Remote-Name`` are stripped for tidiness, ``Remote-Email`` is stripped
    because it is the credential.
    """
    response = await client.get(
        "/api/v1/users/me",
        headers={
            "Remote-User": ATHLETE,
            "Remote-Name": "Someone Else",
            SECRET_HEADER: PROXY_SECRET,
        },
    )
    assert response.status_code == 401, response.text


@pytest.mark.asyncio
async def test_whitespace_around_an_address_is_not_a_second_athlete(
    client, athlete, authelia_mode
) -> None:
    """The follow-through from layer 1's asymmetry.

    Trailing whitespace reaches the app intact, and the raw lookup used to miss
    the existing row and create ``"athlete@example.com   "`` as a new account —
    a second athlete with the same address and none of their data. ``EmailStr``
    normalises it away, so both spellings resolve to the one account.
    """
    response = await client.get(
        "/api/v1/users/me",
        headers={"Remote-Email": f"  {ATHLETE}  ", SECRET_HEADER: PROXY_SECRET},
    )
    assert response.status_code == 200, response.text
    assert response.json()["email"] == ATHLETE


@pytest.mark.asyncio
async def test_a_differently_cased_domain_is_the_same_athlete(
    client, athlete, authelia_mode
) -> None:
    """Because registration normalises the domain, and this path now does too.

    The two paths sharing one validator is the property; this is one case of it.
    A header path that normalised differently would silently fork the account.
    """
    local, _, domain = ATHLETE.partition("@")
    response = await client.get(
        "/api/v1/users/me",
        headers={
            "Remote-Email": f"{local}@{domain.upper()}",
            SECRET_HEADER: PROXY_SECRET,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["email"] == ATHLETE


@pytest.mark.asyncio
async def test_a_differently_cased_local_part_is_a_different_athlete(
    client, athlete, authelia_mode
) -> None:
    """Pinned rather than fixed, because the RFC says the local part is the
    mailbox owner's business and ``EmailStr`` leaves it alone.

    The consequence is real and belongs written down: an identity provider that
    changes the capitalisation of a username gets a new, empty account rather
    than the existing one. Registration behaves identically, so the two paths
    agree — which is the property worth protecting. Normalising only here would
    let one address resolve to two accounts depending on how the athlete signed
    in.
    """
    local, _, domain = ATHLETE.partition("@")
    response = await client.get(
        "/api/v1/users/me",
        headers={
            "Remote-Email": f"{local.upper()}@{domain}",
            SECRET_HEADER: PROXY_SECRET,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["email"] != ATHLETE


@pytest.mark.parametrize(
    ("shape", "value"),
    [
        ("not an address at all", "intruder"),
        ("a bare hostname", "example.com"),
        ("two addresses", f"{INTRUDER}, {ATHLETE}"),
        ("a path", "../../etc/passwd"),
        ("a URL", "https://example.com/athlete"),
        ("SQL-shaped", "' OR 1=1 --"),
        ("YAML-shaped", "athlete@example.com:\n  password: x"),
        ("an unresolvable local name", "athlete@localhost"),
        ("five thousand characters", "x" * 5000 + "@example.com"),
        ("only whitespace", "   "),
    ],
)
@pytest.mark.asyncio
async def test_a_value_that_is_not_an_address_creates_nothing(
    client, authelia_mode, shape, value
) -> None:
    """The header path was the one account-creating path with no validation.

    Whatever bytes arrived became a ``users.email``. The YAML-shaped case is
    there for a specific reason: registration writes this value into Authelia's
    ``users_database.yml``, so a value that is not an address is a value in a
    file that is parsed as structure later.
    """
    response = await client.get(
        "/api/v1/users/me",
        headers={"Remote-Email": value, SECRET_HEADER: PROXY_SECRET},
    )
    assert response.status_code == 401, f"{shape} was accepted"


@pytest.mark.asyncio
async def test_a_display_name_resolves_to_the_address_inside_it(
    client, athlete, authelia_mode
) -> None:
    """``Athlete <athlete@example.com>`` is that athlete, which surprised me.

    ``EmailStr`` parses the RFC 5322 display-name form and returns the address
    alone, so this header authenticates as the existing account rather than
    minting ``"Athlete <athlete@example.com>"`` as a second one — which is what
    the raw lookup used to do. Recorded because a reader would reasonably assume
    a bare address is the only accepted shape, and because registration accepts
    and normalises it identically: the two paths agreeing is the property, and
    this is a case where agreement is less obvious than it looks.
    """
    response = await client.get(
        "/api/v1/users/me",
        headers={"Remote-Email": f"Athlete <{ATHLETE}>", SECRET_HEADER: PROXY_SECRET},
    )
    assert response.status_code == 200, response.text
    assert response.json()["email"] == ATHLETE


@pytest.mark.asyncio
async def test_a_rejected_value_is_not_written_to_the_log(
    client, authelia_mode, caplog
) -> None:
    """An address is personal data even when it is a forgery (#499).

    The length is logged instead, which is what distinguishes a misconfigured
    proxy from something probing — and is the thing an operator can act on.
    """
    sentinel = "wintermute-at-tessier-ashpool"
    with caplog.at_level("WARNING"):
        await client.get(
            "/api/v1/users/me",
            headers={"Remote-Email": sentinel, SECRET_HEADER: PROXY_SECRET},
        )
    assert "is not an email address" in caplog.text
    assert sentinel not in caplog.text
    assert "wintermute" not in caplog.text
    assert str(len(sentinel)) in caplog.text


@pytest.mark.asyncio
async def test_the_first_of_two_identity_headers_wins(
    client, athlete, authelia_mode
) -> None:
    """Measured, not relied on.

    Both proxies delete every occurrence, so a duplicate should never arrive.
    If one does, the behaviour is "first wins" — stated here so that a future
    change to last-wins is a failing test rather than a silent change in which
    of two asserted identities the app believes. Also that the two are never
    joined into one value: a comma-joined pair would fail validation and resolve
    to nobody, which is a 401 for an athlete whose proxy is merely untidy.
    """
    response = await client.get(
        "/api/v1/users/me",
        headers=[
            ("Remote-Email", INTRUDER),
            ("Remote-Email", ATHLETE),
            (SECRET_HEADER, PROXY_SECRET),
        ],
    )
    assert response.status_code == 200, response.text
    assert response.json()["email"] == INTRUDER


@pytest.mark.asyncio
async def test_a_new_identity_does_not_end_in_a_server_error(
    client, authelia_mode
) -> None:
    """The first request of a new Authelia identity used to be a 500.

    ``crud.create_user`` returns an instance with no relationships loaded, and
    this is an auth dependency, so whatever route asked for the user serialised
    it and tripped ``MissingGreenlet``. Only reachable behind a valid secret,
    and still a 500 on the account-creating path of a live forward-auth
    deployment.
    """
    response = await client.get(
        "/api/v1/users/me",
        headers={"Remote-Email": INTRUDER, SECRET_HEADER: PROXY_SECRET},
    )
    assert response.status_code == 200, response.text
    assert response.json()["email"] == INTRUDER


@pytest.mark.asyncio
async def test_header_auth_is_inert_when_authelia_mode_is_off(
    client, athlete, monkeypatch
) -> None:
    """The deployed configuration, for completeness.

    ``AUTHELIA_AUTH_ENABLED`` is true on this deployment, but a self-hoster who
    turns it off must not find the header path still live behind a secret they
    never configured.
    """
    monkeypatch.setattr(auth, "AUTHELIA_AUTH_ENABLED", False)
    monkeypatch.setattr(auth, "AUTHELIA_PROXY_SHARED_SECRET", PROXY_SECRET)
    response = await client.get(
        "/api/v1/users/me",
        headers={"Remote-Email": ATHLETE, SECRET_HEADER: PROXY_SECRET},
    )
    assert response.status_code == 401, response.text


@pytest.mark.asyncio
async def test_a_believed_header_still_outranks_session_revocation(
    client, athlete, monkeypatch
) -> None:
    """Pinned as the known gap it is, not asserted as correct.

    ``get_current_user`` takes the Authelia branch before it looks at a token,
    and a header-authenticated request carries no generation to compare, so
    "sign out everywhere" does not reach it. Behind a valid proxy secret that is
    a property of forward auth rather than a hole — Authelia owns the session —
    but it is the thing to answer before switching forward auth on, and a test
    that quietly started passing for the opposite reason would bury it.

    Revoked before Authelia mode is switched on, because revocation asks for the
    password and in Authelia mode that question goes to the user store.
    """
    revoke = await client.post(
        "/api/v1/auth/sessions/revoke",
        json={"password": "Str0ng!Pass"},
        headers={"Authorization": f"Bearer {athlete}"},
    )
    assert revoke.status_code == 200, revoke.text

    bearer = await client.get(
        "/api/v1/users/me", headers={"Authorization": f"Bearer {athlete}"}
    )
    assert bearer.status_code == 401, "the revocation did not take effect"

    monkeypatch.setattr(auth, "AUTHELIA_AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "AUTHELIA_PROXY_SHARED_SECRET", PROXY_SECRET)
    monkeypatch.setattr(auth, "AUTHELIA_PROXY_SECRET_HEADER", SECRET_HEADER)
    header = await client.get(
        "/api/v1/users/me",
        headers={"Remote-Email": ATHLETE, SECRET_HEADER: PROXY_SECRET},
    )
    assert header.status_code == 200, header.text


# ---------------------------------------------------------------------------
# Layer 3: the deployment files. Is the header deleted on every way in?
# ---------------------------------------------------------------------------
#
# Layers 1 and 2 establish that the app believes exactly the configured header
# names, lower-cased, and nothing adjacent to them. What is left is whether
# anything upstream can hand it one — which is a property of two files, and the
# reason ai-trainer-ops#34 exists: "a configuration decision that lives only in
# the README is one that can quietly disappear on the next Traefik upgrade."

TRUSTED_HEADERS = (
    config.Settings.model_fields["authelia_remote_user_header"].default,
    config.Settings.model_fields["authelia_remote_email_header"].default,
    config.Settings.model_fields["authelia_remote_name_header"].default,
)
"""The names the backend reads, taken from ``Settings`` rather than retyped.

Retyping them is how the two lists drift apart: the backend reads these from the
environment, and both proxies hard-code them. ``deploy/forwarded-vars.yml``
keeps them in ``template_default_only`` for exactly this reason, so the default
in ``config.py`` is the deployed value and this is the real list.
"""


def _backend_router_names() -> set[str]:
    """Traefik routers in ``compose.yml`` whose service is the backend."""
    return set(
        re.findall(
            r"traefik\.http\.routers\.([\w-]+)\.service=backend\b", COMPOSE.read_text()
        )
    )


def _router_middlewares(router: str) -> list[str]:
    match = re.search(
        rf"traefik\.http\.routers\.{re.escape(router)}\.middlewares=(\S+)",
        COMPOSE.read_text(),
    )
    return match.group(1).split(",") if match else []


def test_the_compose_file_actually_defines_backend_routers() -> None:
    """The companion assertion. A regex that matches nothing sweeps nothing."""
    routers = _backend_router_names()
    assert len(routers) >= 3, f"found only {routers}; the sweep below is vacuous"


def test_every_router_that_reaches_the_backend_strips_the_identity_headers() -> None:
    """The first question in ai-trainer-ops#34, answered per router.

    "Is it stripped on all backend routes, or only those named in the middleware
    matcher?" — the middleware is attached per router, and a fourth router added
    for a new path would not inherit it. A router that only redirects is exempt:
    it answers 301 and forwards nothing.
    """
    for router in sorted(_backend_router_names()):
        middlewares = _router_middlewares(router)
        assert middlewares, f"router {router} reaches the backend with no middlewares"
        if any("redirect" in name for name in middlewares):
            continue
        assert any("backend-strip-remote" in name for name in middlewares), (
            f"router {router} reaches the backend without backend-strip-remote; "
            "a request on it can carry a forged Remote-Email"
        )


@pytest.mark.parametrize("header", TRUSTED_HEADERS)
def test_traefik_deletes_every_header_the_backend_trusts(header) -> None:
    """An empty ``customRequestHeaders`` value is a delete, and it has to cover
    all three names, not the two somebody remembered."""
    assert (
        f"backend-strip-remote.headers.customRequestHeaders.{header}=" in COMPOSE.read_text()
    ), f"{header} is trusted by the backend and not stripped by Traefik"


@pytest.mark.parametrize("header", TRUSTED_HEADERS)
def test_the_frontend_nginx_blanks_every_header_the_backend_trusts(header) -> None:
    """The second way in, and the one that is easy to forget (#682).

    The frontend's nginx proxies ``/api`` straight to ``backend:8000`` without
    passing through Traefik's backend router, so Traefik's middleware does not
    cover it. Router priorities mean ``/api`` does not reach here today — a
    routing accident, one label edit from not being true.
    """
    assert f'proxy_set_header {header} ""' in NGINX_CONF.read_text(), (
        f"{header} is trusted by the backend and not blanked by the frontend "
        "nginx, which proxies /api to the backend without passing Traefik's "
        "backend router"
    )


def test_the_frontend_nginx_does_not_forward_a_client_proxy_secret() -> None:
    """Forwarding it would mint exactly the trust it exists to prove."""
    default = config.Settings.model_fields["authelia_proxy_secret_header"].default
    assert f'proxy_set_header {default} ""' in NGINX_CONF.read_text()


def test_the_proxy_injects_the_header_the_backend_reads() -> None:
    """The two halves of the proof-of-transit have to name the same header.

    They were apart once: the backend read ``AUTHELIA_PROXY_SHARED_SECRET`` and
    shipped it in ``.env`` long before anything injected it, so setting it
    *disabled* header trust rather than tightening it (#682). Now that an empty
    secret disables the path outright, a mismatch here means a forward-auth
    deployment answers 401 to everything.
    """
    text = COMPOSE.read_text()
    default = config.Settings.model_fields["authelia_proxy_secret_header"].default
    assert (
        "backend-proxy-secret.headers.customRequestHeaders."
        f"${{AUTHELIA_PROXY_SECRET_HEADER:-{default}}}=" in text
    )
    assert "${AUTHELIA_PROXY_SHARED_SECRET:-}" in text


def test_the_backend_port_is_published_on_loopback_only() -> None:
    """A ``0.0.0.0`` mapping is a route to the backend that no proxy filters."""
    assert '"127.0.0.1:8000:8000"' in COMPOSE.read_text()


def test_no_overlay_can_republish_the_backend_port() -> None:
    """The second half of the question, which the compose file alone cannot answer.

    ai-trainer-ops#34 asked whether the loopback binding holds for the Ansible
    deployment on the Hetzner host, or only locally. It holds because the
    playbook composes ``compose.yml`` with at most ``compose.smtp.yml``, and a
    later ``--file`` wins on ``ports``. So the question is whether any overlay
    touches the backend at all.
    """
    playbook = (REPO_ROOT / "deploy" / "ansible" / "deploy.yml").read_text()
    overlays = set(re.findall(r"--file (compose[\w.]*\.yml)", playbook)) - {"compose.yml"}
    assert overlays == {"compose.smtp.yml"}, (
        f"the playbook layers {overlays or 'nothing'} over compose.yml; check "
        "whether the new overlay republishes the backend port"
    )
    for name in sorted(overlays):
        text = (REPO_ROOT / name).read_text()
        assert "backend:" not in text, (
            f"{name} redefines the backend service, so the loopback binding in "
            "compose.yml is no longer the deployed one"
        )


def test_traefik_does_not_route_containers_by_default() -> None:
    """``exposedbydefault=true`` would give the backend a second, bare router.

    Traefik would generate one from the service alone, carrying no middlewares —
    so the strip list would be attached to the three routers in this file and
    absent from the one nobody declared.
    """
    assert "--providers.docker.exposedbydefault=false" in COMPOSE.read_text()


def test_the_boot_guards_are_still_called_by_the_lifespan() -> None:
    """A guard that is tested and not wired is a control that only looks wired.

    Nothing in the suite runs ``lifespan``, so every call in it is unexercised
    and deleting one would turn no test red. That is the #682 shape exactly: the
    proxy secret was read by the backend and injected by nobody for months,
    because each half was fine on its own.

    Renaming ``warn_if_authelia_proxy_unprotected`` here is how such a call gets
    lost — the function keeps its tests under the new name and the boot sequence
    quietly stops calling anything. Checked by AST rather than by substring so
    that a mention in a comment cannot stand in for a call.
    """
    import ast

    source = (REPO_ROOT / "backend" / "main.py").read_text()
    lifespan = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "lifespan"
    )
    called = {
        node.func.attr
        for node in ast.walk(lifespan)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    for guard in (
        "validate_jwt_secret",
        "warn_if_authelia_header_auth_is_off",
        "warn_if_authelia_user_store_unwritable",
        "warn_if_admin_secret_unusable",
    ):
        assert guard in called, f"lifespan no longer calls {guard}"


def test_the_header_names_are_not_tunable_per_deployment() -> None:
    """What makes :data:`TRUSTED_HEADERS` the real list rather than a guess.

    If a deployment could set ``AUTHELIA_REMOTE_EMAIL_HEADER`` to something else,
    the backend would start trusting a name neither proxy strips, and every
    assertion above would still pass.
    """
    manifest = (REPO_ROOT / "deploy" / "forwarded-vars.yml").read_text()
    tail = manifest[manifest.index("template_default_only:") :]
    for setting in (
        "authelia_remote_user_header",
        "authelia_remote_email_header",
        "authelia_remote_name_header",
    ):
        assert f"- {setting}" in tail, (
            f"{setting} is no longer pinned to its template default, so a "
            "deployment can make the backend trust a header name that Traefik "
            "and nginx do not strip"
        )
