"""Consent to the weekly coach review (ai-trainer-ops#136)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select

import models
from config import settings
from services import coach_review_consent
from tests.conftest import TestSessionLocal

URL = "/api/v1/users/me/coach-review-consent"


@pytest.fixture
def opt_in(monkeypatch):
    monkeypatch.setattr(settings, "coach_review_opt_in", True)


async def _rows():
    async with TestSessionLocal() as db:
        return list(
            (
                await db.scalars(
                    select(models.CoachReviewConsent).order_by(models.CoachReviewConsent.given_at)
                )
            ).all()
        )


@pytest.mark.asyncio
async def test_with_the_opt_in_off_nothing_can_be_given(client, auth_headers):
    response = await client.get(URL, headers=auth_headers)
    assert response.json() == {
        "available": False,
        "consented": False,
        "consentedAt": None,
        "version": coach_review_consent.VERSION,
    }

    assert (await client.put(URL, json={"consented": True}, headers=auth_headers)).status_code == 404
    assert await _rows() == []


@pytest.mark.asyncio
async def test_give_withdraw_and_give_again_keeps_every_consent(client, auth_headers, opt_in):
    assert (await client.get(URL, headers=auth_headers)).json()["consented"] is False

    given = (await client.put(URL, json={"consented": True}, headers=auth_headers)).json()
    assert given["available"] is True and given["consented"] is True
    assert given["consentedAt"] is not None
    # Giving twice keeps the one consent.
    again = (await client.put(URL, json={"consented": True}, headers=auth_headers)).json()
    assert again["consentedAt"] == given["consentedAt"]
    assert len(await _rows()) == 1

    withdrawn = (await client.put(URL, json={"consented": False}, headers=auth_headers)).json()
    assert withdrawn["consented"] is False and withdrawn["consentedAt"] is None
    assert (await client.get(URL, headers=auth_headers)).json()["consented"] is False

    await client.put(URL, json={"consented": True}, headers=auth_headers)
    first, second = await _rows()
    assert first.withdrawn_at is not None and second.withdrawn_at is None
    assert {first.version, second.version} == {coach_review_consent.VERSION}


@pytest.mark.asyncio
async def test_a_new_text_needs_a_new_consent(client, auth_headers, opt_in, monkeypatch):
    await client.put(URL, json={"consented": True}, headers=auth_headers)

    monkeypatch.setattr(coach_review_consent, "VERSION", "2027-01-01")
    response = (await client.get(URL, headers=auth_headers)).json()
    assert response["consented"] is False and response["version"] == "2027-01-01"

    await client.put(URL, json={"consented": True}, headers=auth_headers)
    old, new = await _rows()
    # The old text's consent ends when the new one is given.
    assert old.version == "2026-10-20" and old.withdrawn_at is not None
    assert new.version == "2027-01-01" and new.withdrawn_at is None


@pytest.mark.asyncio
async def test_the_legal_endpoint_says_whether_privacy_describes_the_review(client, monkeypatch):
    assert (await client.get("/api/v1/auth/legal")).json()["coachReviewOptIn"] is False
    monkeypatch.setattr(settings, "coach_review_opt_in", True)
    assert (await client.get("/api/v1/auth/legal")).json()["coachReviewOptIn"] is True


@pytest.mark.asyncio
async def test_withdrawing_without_a_consent_is_harmless(client, auth_headers, opt_in):
    response = await client.put(URL, json={"consented": False}, headers=auth_headers)
    assert response.status_code == 200 and response.json()["consented"] is False
    assert await _rows() == []


def test_the_switch_is_off_everywhere_until_it_is_set():
    root = Path(__file__).resolve().parents[2]
    template = (root / "deploy/ansible/templates/app.env.j2").read_text(encoding="utf-8")
    assert "COACH_REVIEW_OPT_IN={{ coach_review_opt_in | default('false') }}" in template
    compose = (root / "compose.yml").read_text(encoding="utf-8")
    assert "COACH_REVIEW_OPT_IN: ${COACH_REVIEW_OPT_IN:-false}" in compose
    assert "name: COACH_REVIEW_OPT_IN" in (root / "deploy/forwarded-vars.yml").read_text(encoding="utf-8")
    assert settings.model_fields["coach_review_opt_in"].default is False


def test_the_migration_adds_the_table_once(tmp_path):
    path = Path(__file__).parent.parent / "alembic/versions/20261020_000002_coach_review_consents.py"
    spec = importlib.util.spec_from_file_location("_coach_review_consents_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'consents.db'}")

    def run(direction: str) -> None:
        with engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                getattr(migration, direction)()

    run("upgrade")
    run("upgrade")
    assert "coach_review_consents" in sa.inspect(engine).get_table_names()
    run("downgrade")
    run("downgrade")
    assert "coach_review_consents" not in sa.inspect(engine).get_table_names()
