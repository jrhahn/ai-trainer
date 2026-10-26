"""Consent to the privacy policy and to health data at sign-up (ai-trainer-ops#2)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import select

import models
from config import settings
from tests.conftest import TestSessionLocal


async def _register(client, consent: bool | None):
    body = {"name": "Anna", "email": "anna@example.com", "password": "Str0ng!Pass"}
    if consent is not None:
        body["privacyConsent"] = consent
    return await client.post("/api/v1/auth/register", json=body)


async def _consent_at():
    async with TestSessionLocal() as db:
        user = await db.scalar(select(models.User))
        return None if user is None else user.privacy_consent_at


@pytest.mark.asyncio
async def test_a_given_consent_is_kept_with_its_time(client):
    assert (await _register(client, True)).status_code == 200
    assert await _consent_at() is not None


@pytest.mark.asyncio
async def test_without_the_requirement_an_account_without_consent_records_none(client):
    assert (await _register(client, None)).status_code == 200
    assert await _consent_at() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("consent", [None, False])
async def test_production_refuses_a_sign_up_without_consent(client, monkeypatch, consent):
    monkeypatch.setattr(settings, "require_privacy_consent", True)

    resp = await _register(client, consent)

    assert resp.status_code == 400
    assert "privacy policy" in resp.json()["detail"]
    async with TestSessionLocal() as db:
        assert await db.scalar(select(models.User)) is None


@pytest.mark.asyncio
async def test_production_takes_a_sign_up_with_consent(client, monkeypatch):
    monkeypatch.setattr(settings, "require_privacy_consent", True)

    assert (await _register(client, True)).status_code == 200
    assert await _consent_at() is not None


def test_the_production_template_insists_and_the_test_stacks_do_not():
    root = Path(__file__).resolve().parents[2]
    template = (root / "deploy/ansible/templates/app.env.j2").read_text(encoding="utf-8")
    env = dict(line.split("=", 1) for line in template.splitlines() if "=" in line)
    assert env["REQUIRE_PRIVACY_CONSENT"] == "{{ require_privacy_consent | default('true') }}"
    assert "REQUIRE_PRIVACY_CONSENT: ${REQUIRE_PRIVACY_CONSENT:-false}" in (
        root / "compose.yml"
    ).read_text(encoding="utf-8")
    assert settings.model_fields["require_privacy_consent"].default is False


def test_the_migration_adds_the_column_once(tmp_path):
    path = Path(__file__).parent.parent / "alembic/versions/20261018_000006_privacy_consent.py"
    spec = importlib.util.spec_from_file_location("_privacy_consent_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'consent.db'}")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY)"))

    def run(direction: str) -> None:
        with engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                getattr(migration, direction)()

    run("upgrade")
    run("upgrade")
    assert "privacy_consent_at" in {c["name"] for c in sa.inspect(engine).get_columns("users")}
    run("downgrade")
    run("downgrade")
    assert "privacy_consent_at" not in {c["name"] for c in sa.inspect(engine).get_columns("users")}


@pytest.mark.asyncio
async def test_a_refused_sign_up_leaves_the_invitation_unused(client, monkeypatch):
    """The consent check comes before the code is used (ai-trainer-ops#2, #108)."""
    from services import invite_codes

    monkeypatch.setattr(settings, "require_privacy_consent", True)
    monkeypatch.setattr(settings, "signup_invite_only", True)
    async with TestSessionLocal() as db:
        [row] = await invite_codes.create(db, 1, None)
        await db.commit()
        code = row.code
    body = {"name": "Anna", "email": "anna@example.com", "password": "Str0ng!Pass", "inviteCode": code}

    assert (await client.post("/api/v1/auth/register", json=body)).status_code == 400
    async with TestSessionLocal() as db:
        assert await invite_codes.is_open(db, code)
    assert (
        await client.post("/api/v1/auth/register", json={**body, "privacyConsent": True})
    ).status_code == 200
