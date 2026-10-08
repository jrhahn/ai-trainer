"""Gemini is the default provider, and accounts that never chose OpenAI move to it
(ai-trainer-ops#42).

New accounts defaulted to ``openai``. In BYOK-only mode that turned the first
plan a new athlete asked for into "No openai API key configured", on a product
that had told them to bring a *Gemini* key.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from httpx import AsyncClient

import models
from config import settings

MIGRATION_PATH = (
    Path(__file__).parent.parent
    / "alembic"
    / "versions"
    / "20261008_000002_default_ai_provider_gemini.py"
)


async def test_a_new_account_defaults_to_gemini(client: AsyncClient, auth_headers):
    me = await client.get("/api/v1/users/me", headers=auth_headers)
    assert me.json()["aiProvider"] == "gemini"


async def test_with_no_usable_key_the_error_names_gemini(
    client: AsyncClient, auth_headers, monkeypatch
):
    """The message the athlete reads must name the key they were told to get."""
    monkeypatch.setattr(settings, "allow_admin_ai_key_fallback", False)
    response = await client.post("/api/v1/ai/generate-plan", headers=auth_headers, json={})
    assert response.status_code == 402
    assert "gemini" in response.json()["detail"].lower()
    assert "openai" not in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# The data migration
# ---------------------------------------------------------------------------


def _load_migration():
    spec = importlib.util.spec_from_file_location("_default_gemini_42", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(engine, direction: str) -> None:
    migration = _load_migration()
    with engine.begin() as conn:
        context = MigrationContext.configure(conn)
        with Operations.context(context):
            getattr(migration, direction)()


@pytest.fixture()
def engine(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path/'provider.db'}")
    models.User.__table__.create(engine)
    rows = [
        # (id, provider, own openai key)
        ("default-openai", "openai", None),       # got there by default
        ("chose-openai", "openai", "ciphertext"),  # saved a key: a choice
        ("gemini", "gemini", None),
        ("gemini-with-openai-key", "gemini", "ciphertext"),
    ]
    with engine.begin() as conn:
        for user_id, provider, key in rows:
            # Core insert, so the model's Python defaults fill every column
            # this test does not care about.
            conn.execute(
                models.User.__table__.insert().values(
                    id=user_id,
                    email=f"{user_id}@example.com",
                    hashed_password="x",
                    ai_provider=provider,
                    user_openai_api_key=key,
                )
            )
    return engine


def _providers(engine) -> dict[str, str]:
    with engine.connect() as conn:
        return dict(conn.execute(sa.text("SELECT id, ai_provider FROM users")).all())


def test_only_accounts_that_never_chose_openai_move(engine):
    _run(engine, "upgrade")
    assert _providers(engine) == {
        "default-openai": "gemini",
        "chose-openai": "openai",
        "gemini": "gemini",
        "gemini-with-openai-key": "gemini",
    }


def test_the_migration_is_idempotent(engine):
    _run(engine, "upgrade")
    _run(engine, "upgrade")
    assert _providers(engine)["chose-openai"] == "openai"


def test_the_downgrade_changes_nothing(engine):
    _run(engine, "upgrade")
    before = _providers(engine)
    _run(engine, "downgrade")
    assert _providers(engine) == before
