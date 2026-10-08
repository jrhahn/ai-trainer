"""The stub answers without a model, and cannot reach an athlete
(ai-trainer-ops#46).

Four pieces of work were each blocked on having no way to make the model answer
deterministically — the end-to-end suite that has to reach a dashboard with a
plan on it (#46), a judge in CI (#28), a scenario suite (#18), and the layout
defects that only appear behind an onboarded account (#51). None of them can
carry a real provider key.

Most of this file is about the half that is not convenience. A stub that served
a live instance would mean athletes reading placeholder text in the coach's
voice, following a plan built by a loop, with nothing in the UI saying so — and
unlike a crash, nobody would find out. So it is locked twice, at construction
and at use, and the tests for the locks outnumber the tests for the feature.

What the stub returns is driven by the *task* the provider was built for, not by
the text of the prompt. Reading the prompt is the version that rots: a reworded
system message would silently change the answer, and the test that relied on it
would fail somewhere unrelated.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

import schemas
from config import DEV_ENVS, Settings, settings
from services import llm as llm_service
from services.coach_schema import COACH_REPLY_SCHEMA

REPO = Path(__file__).resolve().parents[2]


def _settings_kwargs(**overrides) -> dict:
    base = {
        "jwt_secret": "a-test-secret-that-is-at-least-32-bytes",
        "secrets_encryption_key": Fernet.generate_key().decode(),
    }
    base.update(overrides)
    return base


@pytest.fixture
def stub_on(monkeypatch):
    """The stub active in a development environment, which is the only way."""
    monkeypatch.setattr(settings, "ai_stub_provider", True)
    monkeypatch.setattr(settings, "app_env", "test")


# ---------------------------------------------------------------------------
# It cannot run where an athlete can see it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("app_env", ["production", "prod", "staging", "live"])
def test_settings_refuse_to_construct_with_the_stub_outside_development(app_env):
    """A boot refusal, not a warning.

    The alternative is a process that starts and serves canned coaching, which
    is the failure this exists to make impossible. A deployment that will not
    start gets noticed in minutes; one that answers plausibly does not.
    """
    with pytest.raises(ValueError, match="AI_STUB_PROVIDER"):
        Settings(**_settings_kwargs(app_env=app_env, ai_stub_provider=True))


@pytest.mark.parametrize("app_env", sorted(DEV_ENVS))
def test_every_development_environment_may_use_it(app_env):
    """The allowlist is the one in ``config``, not a copy of it.

    Parametrised over ``DEV_ENVS`` so a new development environment name cannot
    be added to the config and left unable to run the suite that needs this.
    """
    assert Settings(
        **_settings_kwargs(app_env=app_env, ai_stub_provider=True)
    ).ai_stub_provider


def test_it_is_off_without_anyone_doing_anything():
    """Default False, in production, with no mention of the flag."""
    assert not Settings(**_settings_kwargs(app_env="production")).ai_stub_provider


def test_the_second_lock_holds_when_the_flag_is_set_on_a_live_object(monkeypatch, caplog):
    """``Settings`` cannot catch an attribute assigned after construction.

    Which is not hypothetical: ``monkeypatch.setattr(settings, ...)`` does
    exactly that, and so would any future code that flips the flag at runtime.
    ``stub_is_active`` therefore re-checks the environment rather than trusting
    that construction was the only way in.
    """
    monkeypatch.setattr(settings, "ai_stub_provider", True)
    monkeypatch.setattr(settings, "app_env", "production")

    with caplog.at_level("ERROR"):
        assert llm_service.stub_is_active() is False

    assert "AI_STUB_PROVIDER" in caplog.text, "a silent refusal is a refusal nobody fixes"


def test_a_production_request_gets_a_real_provider_not_the_stub(monkeypatch):
    """The lock has to change the outcome, not only return False."""
    monkeypatch.setattr(settings, "ai_stub_provider", True)
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "gemini_api_key", "a-global-key")

    provider = llm_service.get_provider("gemini", task=llm_service.TASK_COACH)
    assert not isinstance(provider, llm_service.StubProvider)


# ---------------------------------------------------------------------------
# What it answers
# ---------------------------------------------------------------------------


def test_the_stub_is_chosen_when_it_is_active(stub_on):
    assert isinstance(
        llm_service.get_provider("gemini", task=llm_service.TASK_COACH),
        llm_service.StubProvider,
    )


def test_it_is_chosen_without_any_key_configured(stub_on, monkeypatch):
    """Before the key checks, on purpose.

    Its whole use is a deployment with no key at all. A harness that had to
    invent one to reach the stub would be configuring the thing it replaces.
    """
    monkeypatch.setattr(settings, "gemini_api_key", "")
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr(settings, "allow_admin_ai_key_fallback", False)

    assert isinstance(
        llm_service.get_provider("gemini", task=llm_service.TASK_PLAN),
        llm_service.StubProvider,
    )


async def test_a_plan_request_yields_days_that_validate(stub_on):
    """The one shape a caller cannot work without.

    ``_generate_plan_days`` validates every entry against ``PlanDay`` and
    retries on failure, so a malformed plan is no plan at all — and the
    dashboard the E2E suite is trying to reach stays empty.
    """
    provider = llm_service.get_provider("gemini", task=llm_service.TASK_PLAN)
    days = json.loads(await provider.chat("system", "user", json_mode=True))["plan"]

    assert len(days) == 14
    assert [schemas.PlanDay.model_validate(day) for day in days]


async def test_the_plan_starts_today_rather_than_on_a_fixed_date(stub_on):
    """A hard-coded fortnight works until the day it quietly stops.

    Days in the past are rejected downstream, so a constant start date would
    turn into a test that passes in the week it was written.
    """
    from datetime import datetime, timezone

    provider = llm_service.get_provider("gemini", task=llm_service.TASK_PLAN)
    days = json.loads(await provider.chat("system", "user", json_mode=True))["plan"]

    assert days[0]["date"] == datetime.now(timezone.utc).date().isoformat()


async def test_a_response_schema_is_answered_with_an_instance_of_itself(stub_on):
    """So any caller that says what shape it wants is served already."""
    provider = llm_service.get_provider("gemini", task=llm_service.TASK_COACH)
    reply = json.loads(
        await provider.chat("system", "user", json_mode=True, response_schema=COACH_REPLY_SCHEMA)
    )

    assert set(reply) == set(COACH_REPLY_SCHEMA["required"])
    assert all(isinstance(value, str) for value in reply.values())


def test_only_required_properties_are_filled():
    """An object carrying every optional field is not what a model returns.

    A caller tested against the generous version would pass here and fail in
    production on the field the real model omitted.
    """
    schema = {
        "type": "OBJECT",
        "properties": {"a": {"type": "STRING"}, "b": {"type": "STRING"}},
        "required": ["a"],
    }
    assert llm_service._instance_of(schema) == {"a": llm_service.STUB_MARKER}


def test_an_array_gets_one_element_rather_than_none():
    """An empty list is a valid instance and a useless fixture.

    The branch worth exercising is the one that handles "there is something";
    a caller wanting none can assert on the count.
    """
    assert llm_service._instance_of({"type": "ARRAY", "items": {"type": "STRING"}}) == [
        llm_service.STUB_MARKER
    ]


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        ({"type": "INTEGER"}, 0),
        ({"type": "NUMBER"}, 0),
        ({"type": "BOOLEAN"}, False),
        ({"type": "STRING", "enum": ["first", "second"]}, "first"),
    ],
)
def test_scalar_shapes(schema, expected):
    assert llm_service._instance_of(schema) == expected


async def test_an_unknown_json_request_is_empty_rather_than_invented(stub_on):
    """`{}` says "nothing", which is honest.

    A made-up shape would give the caller wrong data instead of none, and the
    difference only shows up far from here.
    """
    provider = llm_service.get_provider("gemini", task=llm_service.TASK_CLASSIFY)
    assert json.loads(await provider.chat("system", "user", json_mode=True)) == {}


# ---------------------------------------------------------------------------
# It is recognisable as a stub
# ---------------------------------------------------------------------------


async def test_everything_it_writes_is_marked(stub_on):
    """A reply that escapes a harness must be identifiable on sight.

    Prose and plan alike: a screenshot or a log line should not be mistakable
    for something the coach said.
    """
    provider = llm_service.get_provider("gemini", task=llm_service.TASK_PLAN)

    prose = await provider.chat("system", "user")
    assert llm_service.STUB_MARKER in prose

    days = json.loads(await provider.chat("system", "user", json_mode=True))["plan"]
    assert all(llm_service.STUB_MARKER in day["title"] for day in days)
    assert all(llm_service.STUB_MARKER in day["description"] for day in days)


async def test_it_does_not_pretend_to_coach(stub_on):
    """Structurally valid, semantically empty — and that is the intent.

    Plausible advice is a thing someone eventually screenshots, quotes or
    trusts. The prose says nothing about training on purpose.
    """
    provider = llm_service.get_provider("gemini", task=llm_service.TASK_COACH)
    prose = (await provider.chat("system", "user")).lower()

    for word in ("ftp", "threshold", "interval", "zone", "recovery", "watts"):
        assert word not in prose, f"the stub used coaching vocabulary: {word}"


async def test_chat_history_answers_like_chat(stub_on):
    """Both halves of the protocol, since callers use either."""
    provider = llm_service.get_provider("gemini", task=llm_service.TASK_PLAN)

    one = json.loads(await provider.chat("system", "user", json_mode=True))
    other = json.loads(
        await provider.chat_history("system", [{"role": "user", "content": "u"}], json_mode=True)
    )
    assert one == other


def test_it_satisfies_the_protocol(stub_on):
    """Structural, so a change to ``LLMProvider`` fails here rather than at a
    call site that happens to be exercised."""
    assert isinstance(llm_service.StubProvider(), llm_service.LLMProvider)


# ---------------------------------------------------------------------------
# It is usable for the thing it was built for
# ---------------------------------------------------------------------------


async def test_the_real_plan_path_produces_a_plan_with_no_key_anywhere(stub_on, monkeypatch):
    """Through ``generate_training_plan``, not the provider in isolation.

    The isolated tests above say the stub returns a well-shaped plan. This says
    the pipeline that consumes it agrees: `_generate_plan_days` validates every
    entry against ``PlanDay`` and retries on failure, so a shape that is merely
    plausible would come back as an empty plan rather than an error.

    A stub nothing consumes is just a class, and the four pieces of work waiting
    on this one all need exactly this call to succeed without a key.
    """
    monkeypatch.setattr(settings, "gemini_api_key", "")
    monkeypatch.setattr(settings, "openai_api_key", "")

    from services import ai_service

    profile = {
        "name": "Alex",
        "trainingGoal": "general_fitness",
        "weeklyHours": 8,
        "fitnessLevel": "intermediate",
        "bikeType": "road",
        "followsTrainingPlan": False,
    }
    days = await ai_service.generate_training_plan(profile, None, None)

    assert len(days) == 14
    assert all(llm_service.STUB_MARKER in day["title"] for day in days)


def test_the_flag_is_deliberately_not_forwarded_into_the_container():
    """The one variable this repo must *not* wire into `compose.yml`.

    Every other setting is forwarded because a variable absent from the backend
    `environment:` block never reaches the process — that has caused #612, #617,
    #684, #694, #700 and a near-miss on #754, and the habit it taught is "add it
    to compose". Here absence is the safe direction: unforwarded means the stub
    cannot be switched on from a `.env` at all, which is one fewer way for a
    deployment to serve canned coaching.

    Pinned with the reasoning, because someone applying the usual rule would
    otherwise helpfully "fix" it.
    """
    compose = (REPO / "compose.yml").read_text(encoding="utf-8")
    assert "AI_STUB_PROVIDER" not in compose
