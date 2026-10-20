"""Sign-up by single-use invitation for the closed friends round (ops#108 option (b), #96)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from httpx import AsyncClient
from sqlalchemy import select

import models
from config import settings
from services import invite_codes
from tests.conftest import TestSessionLocal

PASSWORD = "Str0ng!Pass"


@pytest.fixture
def invite_only(monkeypatch):
    monkeypatch.setattr(settings, "signup_invite_only", True)
    monkeypatch.setattr(settings, "admin_password", "super-secret")


async def _admin(client: AsyncClient) -> dict[str, str]:
    resp = await client.post("/api/v1/admin/login", json={"password": "super-secret"})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _register(client: AsyncClient, email: str, code: str | None = None):
    body = {"name": "Anna", "email": email, "password": PASSWORD}
    if code is not None:
        body["inviteCode"] = code
    return await client.post("/api/v1/auth/register", json=body)


def test_a_code_reads_off_a_phone_and_survives_how_it_is_typed():
    code = invite_codes.new_code()

    assert len(code) == 12 and code.count("-") == 2
    assert not set(code.replace("-", "")) & set("01OIL")
    assert invite_codes.normalize(f"  {code.lower().replace('-', ' ')} ") == code
    assert invite_codes.normalize("") is None
    assert invite_codes.normalize(" -- ") is None
    # A tail after the ten characters is not part of the code (ops#165).
    assert invite_codes.normalize("ABCD-EFGH-JK-MORE") == "ABCD-EFGH-JK"
    assert invite_codes.normalize(f"{code}chr") == code
    assert invite_codes.normalize("ABCD-EF") == "ABCD-EF"


@pytest.mark.asyncio
async def test_open_sign_up_needs_no_code(client):
    assert (await client.get("/api/v1/auth/signup")).json() == {"inviteOnly": False}
    assert (await _register(client, "open@example.com")).status_code == 200


@pytest.mark.asyncio
async def test_an_invitation_opens_one_account_and_only_one(client, invite_only):
    assert (await client.get("/api/v1/auth/signup")).json() == {"inviteOnly": True}
    admin = await _admin(client)
    made = await client.post(
        "/api/v1/admin/invite-codes", headers=admin, json={"count": 2, "note": "for Anna"}
    )
    assert made.status_code == 201
    codes = made.json()["codes"]
    assert len(codes) == 2 and all(c["note"] == "for Anna" and c["usedAt"] is None for c in codes)
    code = codes[0]["code"]

    assert (await _register(client, "anna@example.com", code.lower())).status_code == 200
    # Used: neither a second person nor a retry gets in with it.
    again = await _register(client, "ben@example.com", code)
    assert again.status_code == 403
    assert "invitation only" in again.json()["detail"]

    listed = (await client.get("/api/v1/admin/invite-codes", headers=admin)).json()
    used = {c["code"]: c["usedAt"] for c in listed["codes"]}
    assert used[code] is not None and used[codes[1]["code"]] is None
    assert listed["inviteOnly"] is True

    # The second code arrives with a tail picked up on the way (ops#165).
    tailed = await _register(client, "carla@example.com", f"{codes[1]['code']}chr")
    assert tailed.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [None, "", "NOPE-NOPE-NO"])
async def test_no_or_a_wrong_code_is_refused_without_spending_a_sign_up(
    client, invite_only, monkeypatch, code
):
    spent = []
    monkeypatch.setattr(
        "routers.auth_router.enforce_registration_rate_limit", lambda: spent.append(1)
    )

    resp = await _register(client, "stranger@example.com", code)

    assert resp.status_code == 403
    assert spent == []
    async with TestSessionLocal() as db:
        assert await db.scalar(select(models.User)) is None


@pytest.mark.asyncio
async def test_an_address_already_taken_leaves_the_code_unused(client, invite_only, monkeypatch):
    monkeypatch.setattr(settings, "signup_invite_only", False)
    await _register(client, "taken@example.com")
    monkeypatch.setattr(settings, "signup_invite_only", True)
    admin = await _admin(client)
    code = (
        await client.post("/api/v1/admin/invite-codes", headers=admin, json={})
    ).json()["codes"][0]["code"]

    assert (await _register(client, "taken@example.com", code)).status_code == 409
    assert (await _register(client, "fresh@example.com", code)).status_code == 200


@pytest.mark.asyncio
async def test_two_sign_ups_racing_for_one_code_cannot_both_use_it():
    async with TestSessionLocal() as db:
        [row] = await invite_codes.create(db, 1, None)
        await db.commit()
        code = row.code
    async with TestSessionLocal() as first, TestSessionLocal() as second:
        assert await invite_codes.is_open(first, code) and await invite_codes.is_open(second, code)
        assert await invite_codes.redeem(first, code) is True
        await first.commit()
        assert await invite_codes.redeem(second, code) is False
    assert await invite_codes.redeem(second, None) is False


@pytest.mark.asyncio
async def test_an_unused_invitation_can_be_withdrawn_and_a_used_one_stays(client, invite_only):
    admin = await _admin(client)
    codes = (
        await client.post("/api/v1/admin/invite-codes", headers=admin, json={"count": 2})
    ).json()["codes"]
    await _register(client, "anna@example.com", codes[0]["code"])

    assert (
        await client.delete(f"/api/v1/admin/invite-codes/{codes[1]['id']}", headers=admin)
    ).status_code == 204
    assert (
        await client.delete(f"/api/v1/admin/invite-codes/{codes[0]['id']}", headers=admin)
    ).status_code == 404
    assert (
        await client.delete("/api/v1/admin/invite-codes/unknown", headers=admin)
    ).status_code == 404
    assert (await _register(client, "ben@example.com", codes[1]["code"])).status_code == 403
    left = (await client.get("/api/v1/admin/invite-codes", headers=admin)).json()["codes"]
    assert [c["id"] for c in left] == [codes[0]["id"]]


@pytest.mark.asyncio
async def test_only_the_admin_makes_invitations(client, auth_headers, invite_only):
    assert (await client.get("/api/v1/admin/invite-codes")).status_code == 401
    assert (
        await client.post("/api/v1/admin/invite-codes", headers=auth_headers, json={})
    ).status_code == 403
    admin = await _admin(client)
    too_many = await client.post("/api/v1/admin/invite-codes", headers=admin, json={"count": 21})
    assert too_many.status_code == 422


def test_production_asks_for_an_invitation_and_the_test_stacks_do_not():
    root = Path(__file__).resolve().parents[2]
    template = (root / "deploy/ansible/templates/app.env.j2").read_text(encoding="utf-8")
    assert "SIGNUP_INVITE_ONLY={{ signup_invite_only | default('true') }}" in template
    assert "SIGNUP_INVITE_ONLY: ${SIGNUP_INVITE_ONLY:-false}" in (root / "compose.yml").read_text(
        encoding="utf-8"
    )
    assert settings.model_fields["signup_invite_only"].default is False


def test_the_migration_adds_the_table_once(tmp_path):
    path = Path(__file__).parent.parent / "alembic/versions/20261018_000005_invite_codes.py"
    spec = importlib.util.spec_from_file_location("_invite_codes_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'invites.db'}")

    def run(direction: str) -> None:
        with engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                getattr(migration, direction)()

    run("upgrade")
    run("upgrade")
    assert "invite_codes" in sa.inspect(engine).get_table_names()
    run("downgrade")
    run("downgrade")
    assert "invite_codes" not in sa.inspect(engine).get_table_names()



@pytest.mark.asyncio
async def test_the_privacy_page_names_who_runs_it_from_configuration(client, monkeypatch):
    """ai-trainer-ops#2, #15: set as variables, never guessed."""
    assert (await client.get("/api/v1/auth/legal")).json() == {
        "operatorName": None,
        "contactEmail": None,
        "coachReviewOptIn": False,
    }
    monkeypatch.setattr(settings, "operator_name", " Jane Doe ")
    monkeypatch.setattr(settings, "contact_email", "hello@example.com")
    assert (await client.get("/api/v1/auth/legal")).json() == {
        "operatorName": "Jane Doe",
        "contactEmail": "hello@example.com",
        "coachReviewOptIn": False,
    }
