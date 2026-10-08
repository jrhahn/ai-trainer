"""Request smuggling through the real proxy pair (ai-trainer-ops#34).

``test_remote_user_boundary`` measures one end: uvicorn answers 400 to every
shape two parsers could disagree about, before the app runs. What it could not
measure is the other end — whether Traefik *normalises* one of those shapes into
something well-formed on the way, which would hand uvicorn a clean request
carrying a header the strip middleware was supposed to remove. That needs the
pair, and this file runs it: the Traefik that ``compose.yml`` pins, in front of
the uvicorn that ``pyproject.toml`` pins, with the strip middleware read from
the ``compose.yml`` labels themselves rather than restated here.

The question asked of every shape is the one that matters, not whether a 400
came back: **did the application ever see a ``Remote-*`` header carrying the
victim's address, or a request it was never sent?** A shape Traefik rejects,
and a shape Traefik forwards with the header gone, both pass. A smuggled second
request that reaches the app fails, whatever status the first one got.

Where it runs:

* **Docker** (CI): ``traefik:<version from compose.yml>``, exactly what
  production runs.
* **``TRAEFIK_BIN``** (a local binary): for iterating without Docker. Not the
  production version, so a pass here is a hint, not the evidence.
* Neither: skipped — unless ``REQUIRE_PROXY_PAIR`` is set, which CI does, so
  a runner without Docker fails instead of skipping silently.

The TLS hop is not part of it: production terminates TLS at Traefik, and the
header parsing and the middleware run on the decrypted request either way.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import uuid
from pathlib import Path

import pytest
import uvicorn
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "compose.yml"
HOST = "trainlikea.pro"
VICTIM = "athlete@example.com"

# ---------------------------------------------------------------------------
# What production configures, read from compose.yml
# ---------------------------------------------------------------------------


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text())


def traefik_image() -> str:
    return _compose()["services"]["traefik"]["image"]


def stripped_headers() -> list[str]:
    """The headers ``backend-strip-remote`` blanks, from the backend's labels."""
    labels = _compose()["services"]["backend"]["labels"]
    prefix = "traefik.http.middlewares.backend-strip-remote.headers.customRequestHeaders."
    names = []
    for label in labels:
        if label.startswith(prefix):
            name, _, value = label[len(prefix):].partition("=")
            assert value == "", f"{name} is set, not stripped"
            names.append(name)
    return names


def test_the_config_this_file_reproduces_is_the_one_compose_runs():
    """Anti-vacuity: an empty strip list would make every check below pass."""
    assert re.fullmatch(r"traefik:v\d+\.\d+\.\d+", traefik_image())
    assert {"Remote-User", "Remote-Email", "Remote-Name", "Remote-Groups"} <= set(
        stripped_headers()
    )


# ---------------------------------------------------------------------------
# The backend end: the pinned uvicorn, serving an app that writes down
# everything it is handed
# ---------------------------------------------------------------------------

_SEEN: list[dict] = []


async def _record(scope, receive, send) -> None:
    if scope["type"] != "http":  # pragma: no cover - lifespan only
        return
    _SEEN.append(
        {
            "path": scope["path"],
            "headers": [
                (k.decode("latin-1").lower(), v.decode("latin-1"))
                for k, v in scope["headers"]
            ],
        }
    )
    body = json.dumps({"ok": True}).encode()
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


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _wait_for(port: int, *, path: str = "/healthz", seconds: float = 30) -> bool:
    deadline = time.monotonic() + seconds
    request = f"GET {path} HTTP/1.1\r\nHost: {HOST}\r\nConnection: close\r\n\r\n"
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1) as sock:
                sock.sendall(request.encode())
                if sock.recv(64).startswith(b"HTTP/1.1 200"):
                    return True
        except OSError:
            pass
        time.sleep(0.25)
    return False


# ---------------------------------------------------------------------------
# The front end: Traefik
# ---------------------------------------------------------------------------


def _dynamic_config(backend_port: int) -> dict:
    return {
        "http": {
            "routers": {
                "backend": {
                    # The production rule's path half; the host half is the
                    # Host header these requests carry.
                    "rule": f"Host(`{HOST}`) && (PathPrefix(`/api`) || Path(`/healthz`))",
                    "entryPoints": ["web"],
                    "middlewares": ["backend-strip-remote"],
                    "service": "backend",
                }
            },
            "middlewares": {
                "backend-strip-remote": {
                    "headers": {
                        "customRequestHeaders": {name: "" for name in stripped_headers()}
                    }
                }
            },
            "services": {
                "backend": {
                    "loadBalancer": {
                        "servers": [{"url": f"http://127.0.0.1:{backend_port}"}]
                    }
                }
            },
        }
    }


def _traefik_command(config_dir: Path, port: int) -> tuple[list[str], str] | None:
    args = [
        f"--entrypoints.web.address=127.0.0.1:{port}",
        "--providers.file.filename=/etc/traefik-test/dynamic.yml",
        "--log.level=ERROR",
    ]
    if shutil.which("docker"):
        name = f"proxy-pair-{uuid.uuid4().hex[:8]}"
        return (
            [
                "docker", "run", "--rm", "--name", name, "--network", "host",
                "-v", f"{config_dir}:/etc/traefik-test:ro",
                traefik_image(), *args,
            ],
            name,
        )
    binary = os.environ.get("TRAEFIK_BIN")
    if binary:
        local = [a.replace("/etc/traefik-test", str(config_dir)) for a in args]
        return [binary, *local], ""
    return None


@pytest.fixture(scope="module")
def pair(tmp_path_factory):
    backend_port, front_port = _free_port(), _free_port()
    config_dir = tmp_path_factory.mktemp("traefik")
    (config_dir / "dynamic.yml").write_text(yaml.safe_dump(_dynamic_config(backend_port)))

    command = _traefik_command(config_dir, front_port)
    if command is None:
        if os.environ.get("REQUIRE_PROXY_PAIR"):
            pytest.fail("REQUIRE_PROXY_PAIR is set but neither docker nor TRAEFIK_BIN is")
        pytest.skip("needs docker or TRAEFIK_BIN to run the real proxy pair")

    server = uvicorn.Server(
        uvicorn.Config(_record, host="127.0.0.1", port=backend_port, log_level="critical")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    argv, container = command
    proxy = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        if not _wait_for(front_port, seconds=90):
            proxy.terminate()
            _, err = proxy.communicate(timeout=10)
            pytest.fail(f"Traefik did not come up: {err.decode(errors='replace')[-2000:]}")
        yield Pair(front_port)
    finally:
        if container:
            subprocess.run(["docker", "stop", container], capture_output=True)
        proxy.terminate()
        try:
            proxy.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            proxy.kill()
        server.should_exit = True
        thread.join(timeout=5)


class Pair:
    def __init__(self, port: int) -> None:
        self.port = port

    def send(self, raw: bytes, *, settle: float = 1.0) -> tuple[bytes, list[dict]]:
        """Send raw bytes to Traefik; return its response and what the app saw."""
        before = len(_SEEN)
        chunks = []
        with socket.create_connection(("127.0.0.1", self.port), timeout=3) as sock:
            sock.sendall(raw)
            sock.settimeout(settle)
            try:
                while True:
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
            except socket.timeout:
                pass
        # A smuggled request is processed on Traefik's pooled upstream
        # connection, possibly after our socket closed; give it a moment.
        time.sleep(0.3)
        return b"".join(chunks), _SEEN[before:]


def _carries_victim(seen: list[dict]) -> list[tuple[str, str]]:
    stripped = {name.lower() for name in stripped_headers()}
    return [
        (name, value)
        for request in seen
        for name, value in request["headers"]
        if VICTIM in value and name in stripped
    ]


def _request(header_block: bytes, *, path: str = "/api/v1/users/me", body: bytes = b"") -> bytes:
    return (
        f"GET {path} HTTP/1.1\r\nHost: {HOST}\r\n".encode()
        + header_block
        + b"\r\n"
        + body
    )


# ---------------------------------------------------------------------------
# The pair, measured
# ---------------------------------------------------------------------------


def test_a_plain_request_goes_through_the_pair(pair):
    """Anti-vacuity: the route works, so a missing header below means stripped."""
    response, seen = pair.send(_request(b"Connection: close\r\n"))
    assert response.startswith(b"HTTP/1.1 200")
    assert [r["path"] for r in seen] == ["/api/v1/users/me"]


@pytest.mark.parametrize("name", ["Remote-Email", "remote-email", "REMOTE-EMAIL", "Remote-User"])
def test_a_well_formed_identity_header_never_reaches_the_app(pair, name):
    response, seen = pair.send(
        _request(f"{name}: {VICTIM}\r\nConnection: close\r\n".encode())
    )
    assert response.startswith(b"HTTP/1.1 200")
    assert _carries_victim(seen) == []


def test_a_repeated_identity_header_is_stripped_every_time(pair):
    response, seen = pair.send(
        _request(
            f"Remote-Email: nobody@example.com\r\nRemote-Email: {VICTIM}\r\n"
            "Connection: close\r\n".encode()
        )
    )
    assert _carries_victim(seen) == []


@pytest.mark.parametrize(
    ("attack", "header_block"),
    [
        ("space before the colon", f"Remote-Email : {VICTIM}\r\n".encode()),
        ("obs-fold continuation", f"Remote-Email: nobody@example.com\r\n\t{VICTIM}\r\n".encode()),
        ("bare LF terminator", f"Remote-Email: {VICTIM}\n".encode()),
        ("NUL in the value", f"Remote-Email: {VICTIM}".encode() + b"\x00\r\n"),
        ("CR in the value", f"Remote-Email: {VICTIM}".encode() + b"\rX\r\n"),
        ("tab before the value", f"Remote-Email:\t{VICTIM}\r\n".encode()),
    ],
)
def test_a_malformed_identity_header_is_not_normalised_into_a_clean_one(
    pair, attack, header_block
):
    """The half #34 left open: does Traefik turn one of these into ``Remote-Email``?"""
    response, seen = pair.send(_request(header_block + b"Connection: close\r\n"))
    assert _carries_victim(seen) == [], f"{attack}: {response[:80]!r}"


def test_an_underscore_spelling_passes_the_proxy_unchanged(pair):
    """Not stripped, and not normalised into ``Remote-Email`` either.

    Traefik forwards ``Remote_Email`` verbatim. That is safe only because the
    app treats it as a different header, which ``test_remote_user_boundary``
    pins at both layers (httptools keeps the underscore; the app reads only the
    configured names). This test pins the proxy's half of that, so a Traefik
    upgrade that starts folding ``_`` into ``-`` fails here first.
    """
    _, seen = pair.send(_request(f"Remote_Email: {VICTIM}\r\nConnection: close\r\n".encode()))
    headers = [h for request in seen for h in request["headers"]]
    assert ("remote_email", VICTIM) in headers
    assert _carries_victim(seen) == []


_SMUGGLED = (
    f"GET /api/smuggled HTTP/1.1\r\nHost: {HOST}\r\nRemote-Email: {VICTIM}\r\n\r\n"
).encode()


@pytest.mark.parametrize(
    ("attack", "framing", "body"),
    [
        (
            "CL.TE: Content-Length and Transfer-Encoding",
            b"Content-Length: " + str(5 + len(_SMUGGLED)).encode() + b"\r\n"
            b"Transfer-Encoding: chunked\r\n",
            b"0\r\n\r\n" + _SMUGGLED,
        ),
        (
            "TE.CL: Transfer-Encoding and a short Content-Length",
            b"Transfer-Encoding: chunked\r\nContent-Length: 3\r\n",
            hex(len(_SMUGGLED))[2:].encode() + b"\r\n" + _SMUGGLED + b"\r\n0\r\n\r\n",
        ),
        (
            "two disagreeing Content-Lengths",
            b"Content-Length: 0\r\nContent-Length: " + str(len(_SMUGGLED)).encode() + b"\r\n",
            _SMUGGLED,
        ),
        (
            "obfuscated Transfer-Encoding",
            b"Content-Length: " + str(5 + len(_SMUGGLED)).encode() + b"\r\n"
            b"Transfer-Encoding: xchunked\r\n",
            b"0\r\n\r\n" + _SMUGGLED,
        ),
    ],
)
def test_no_second_request_is_smuggled_past_the_proxy(pair, attack, framing, body):
    """A desync's second half is a whole request the proxy never vetted.

    The tell is a count, not a path. The client wrote the trailing request on
    its own connection, so a proxy that reads it as a *second request* — as Go's
    parser does for CL.TE, taking chunked over Content-Length — vets it, strips
    it and answers it: two responses, two requests at the app, both clean. That
    is pipelining, and it is correct. Smuggling is the proxy seeing one request
    while the app sees two, so the app may never have handled more requests
    than the client got answers to, and none of them may carry the victim.
    """
    response, seen = pair.send(_request(framing, path="/api/v1/ping", body=body))
    answered = response.count(b"HTTP/1.1 ")
    assert len(seen) <= answered, (
        f"{attack}: the app handled {len(seen)} requests, the client got "
        f"{answered} responses — one was never vetted by the proxy"
    )
    assert _carries_victim(seen) == [], f"{attack}: {response[:80]!r}"
