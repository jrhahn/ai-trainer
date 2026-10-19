#!/usr/bin/env python3
"""Start the production Compose stack from this commit and sign in through it.

ai-trainer-ops#140. The rest of CI runs the backend under uvicorn with SQLite
and without Authelia. Both "login down for everyone" incidents
(2026-09-19 and 2026-09-25, runbook/incidents.md in the ops repository) were
in the layer that leaves out: the images, Postgres, Authelia, and the user
store on a bind mount that two containers share. This script starts that
layer as compose.yml describes it, with throwaway secrets and the production
settings that sign-up goes through. Then it does over HTTP what an athlete
does:

1. the admin signs in and makes an invitation
2. a new athlete solves the captcha and registers with it
3. they sign in and read their own account
4. authelia and the backend restart, and they sign in again

Step 4 repeats the order of the second incident. Authelia's entrypoint
re-owns its config directory on every start (#696), so a stack can work on
its first boot and lock everyone out on its second.

The stack runs on a copy of HEAD (`git archive`), because registration writes
authelia/users_database.yml through the bind mount. Uncommitted changes are
not part of the run.

Usage: python3 scripts/compose_smoke.py  (needs git, and Docker with Compose)
"""

import base64
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

API = "http://127.0.0.1:8000/api/v1"
PROJECT = "ai-trainer-smoke"
SERVICES = ("postgres", "authelia", "backend", "frontend")


class SmokeFailed(Exception):
    pass


def throwaway_env() -> dict[str, str]:
    # The image creates a user and a group with these ids. The group id is the
    # uid as well, because a desktop user's own group (100 on many) already
    # exists in the base image.
    uid = str(os.getuid())
    return {
        # As the production template sets them (deploy/ansible/templates/app.env.j2).
        "APP_ENV": "production",
        "SIGNUP_INVITE_ONLY": "true",
        "REQUIRE_PRIVACY_CONSENT": "true",
        "CAPTCHA_ENABLED": "true",
        "VITE_BACKEND_URL": "",
        # The playbook makes APP_UID own authelia/ on the host. Here the copy
        # belongs to whoever runs this, so the backend runs as them.
        "APP_UID": uid,
        "APP_GID": uid,
        "POSTGRES_PASSWORD": secrets.token_hex(16),
        "JWT_SECRET": secrets.token_hex(32),
        "SECRETS_ENCRYPTION_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
        "ADMIN_PASSWORD": secrets.token_hex(16),
        "AUTHELIA_IDENTITY_VALIDATION_RESET_PASSWORD_JWT_SECRET": secrets.token_hex(32),
        "AUTHELIA_SESSION_SECRET": secrets.token_hex(32),
        "AUTHELIA_STORAGE_ENCRYPTION_KEY": secrets.token_hex(32),
    }


def compose(work: Path, *args: str, check: bool = True, capture: bool = False):
    return subprocess.run(
        ["docker", "compose", "--project-name", PROJECT, "--project-directory", str(work),
         "--file", str(work / "compose.yml"), "--env-file", str(work / ".env"), *args],
        check=check, capture_output=capture, text=True,
    )


def call(method: str, path: str, wanted: int, body: dict | None = None, token: str | None = None):
    request = urllib.request.Request(
        API + path, method=method, data=None if body is None else json.dumps(body).encode()
    )
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, raw = error.code, error.read()
    if status != wanted:
        raise SmokeFailed(f"{method} {path}: HTTP {status}, expected {wanted}: {raw[:300]!r}")
    return json.loads(raw) if raw else None


def solve(challenge: dict) -> dict:
    """The proof of work the sign-up form does (services/captcha.py)."""
    for number in range(challenge["maxnumber"] + 1):
        if hashlib.sha256(f"{challenge['salt']}{number}".encode()).hexdigest() == challenge["challenge"]:
            return {key: challenge[key] for key in ("challenge", "salt", "signature")} | {"number": number}
    raise SmokeFailed("no number up to maxnumber reproduces the captcha challenge")


def sign_in(email: str, password: str) -> None:
    session = call("POST", "/auth/login", 200, {"email": email, "password": password})
    me = call("GET", "/users/me", 200, token=session["access_token"])
    if me.get("email") != email:
        raise SmokeFailed(f"signed in, but /users/me answers for {me.get('email')!r}")
    print(f"ok: {email} signed in and read their account", flush=True)


def register(admin_password: str) -> tuple[str, str]:
    admin = call("POST", "/admin/login", 200, {"password": admin_password})
    codes = call("POST", "/admin/invite-codes", 201, {"count": 1, "note": "compose smoke"},
                 token=admin["access_token"])
    code = next(c["code"] for c in codes["codes"] if c["usedAt"] is None)
    print("ok: the admin signed in and made an invitation", flush=True)

    email, password = f"smoke-{secrets.token_hex(4)}@example.com", secrets.token_urlsafe(24)
    call("POST", "/auth/register", 204, {
        "name": "Compose Smoke", "email": email, "password": password,
        "privacyConsent": True, "inviteCode": code,
        "captcha": solve(call("GET", "/auth/captcha/challenge", 200)),
    })
    print("ok: registered with the invitation", flush=True)
    return email, password


def check_frontend(work: Path) -> None:
    page = compose(work, "exec", "-T", "frontend", "wget", "-qO-", "http://127.0.0.1/",
                   check=False, capture=True)
    if '<div id="root">' not in page.stdout:
        raise SmokeFailed(f"the frontend container does not serve the app: {page.stderr[:300]!r}")
    print("ok: the frontend serves the app", flush=True)


def main() -> int:
    root = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                          capture_output=True, text=True, check=True).stdout.strip()
    work = Path(tempfile.mkdtemp(prefix="compose-smoke-"))
    tree = subprocess.run(["git", "-C", root, "archive", "HEAD"], capture_output=True, check=True)
    subprocess.run(["tar", "-x", "-C", str(work)], input=tree.stdout, check=True)
    env = throwaway_env()
    (work / ".env").write_text("".join(f"{key}={value}\n" for key, value in env.items()))

    failed = True
    try:
        compose(work, "up", "--detach", "--build", "--wait", "--wait-timeout", "300", *SERVICES)
        email, password = register(env["ADMIN_PASSWORD"])
        sign_in(email, password)
        check_frontend(work)
        compose(work, "restart", "authelia", "backend")
        compose(work, "up", "--detach", "--wait", "--wait-timeout", "180", "authelia", "backend")
        sign_in(email, password)
        failed = False
    except (SmokeFailed, subprocess.CalledProcessError) as error:
        print(f"FAIL: {error}", file=sys.stderr, flush=True)
    finally:
        if failed:
            compose(work, "ps", "--all", check=False)
            compose(work, "logs", "--no-color", "--tail", "200", check=False)
        compose(work, "down", "--volumes", "--remove-orphans", "--rmi", "local", check=False)
        shutil.rmtree(work, ignore_errors=True)
        if work.exists():
            # Typically after a failure: a container re-owned files in the copy.
            print(f"note: {work} holds files owned by another user; remove it with sudo")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
