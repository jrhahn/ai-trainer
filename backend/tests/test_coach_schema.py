"""The coach reply schema, and the two ways it could silently go wrong (#558).

``response_mime_type: application/json`` is a request; a ``response_schema`` is
a constraint. Production showed the difference — three prose replies in a row
that nothing could parse. But a schema is also a new way to lose data: a field
the coach may write today and the schema omits stops reaching the plan
tomorrow, with no error anywhere. These tests exist for that risk more than for
the happy path.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

import schemas
import services.ai_service as ai_service
from services.coach_schema import COACH_REPLY_SCHEMA


def test_the_sdk_accepts_the_schema():
    """The dialect is Gemini's, and a wrong one only fails at request time.

    ``GenerateContentConfig`` keeps a raw dict as-is, so building a config
    proves nothing; ``types.Schema`` is the model that actually parses it.
    """
    from google.genai import types

    parsed = types.Schema.model_validate(COACH_REPLY_SCHEMA)

    assert parsed.type == types.Type.OBJECT
    assert parsed.properties is not None


def test_the_plan_update_covers_every_field_the_persist_gate_accepts():
    """The guard against silently narrowing what the coach can change.

    ``PlanDayUpdateSchema`` is the canonical shape a plan update is validated
    against. Anything it accepts and this schema omits becomes unreachable for
    the coach — the drift-bug class of #422/#424, where a change is promised in
    prose and never lands in the stored day. Walking the model means a field
    added there fails here instead of quietly going missing.
    """
    item = COACH_REPLY_SCHEMA["properties"]["planUpdates"]["items"]

    expected = {
        field.alias or name
        for name, field in schemas.PlanDayUpdateSchema.model_fields.items()
    }

    assert expected <= set(item["properties"])
    assert expected <= set(item["propertyOrdering"])


def test_the_model_reasons_before_it_answers():
    """Gemini emits properties in ``propertyOrdering`` order.

    The prompt asks for reasoning in "thinking" *before* the answer. A schema
    that ordered "response" first would delete the chain of thought — a quality
    regression that no assertion about the JSON shape would catch.
    """
    order = COACH_REPLY_SCHEMA["propertyOrdering"]

    assert order.index("thinking") < order.index("response")


def test_only_the_answer_itself_is_required():
    """A required plan update would push the model into inventing edits."""
    assert COACH_REPLY_SCHEMA["required"] == ["thinking", "response"]


def test_every_field_the_parser_reads_can_be_produced():
    """The schema and the reader have to agree, or a field is dead on arrival."""
    produced = set(COACH_REPLY_SCHEMA["properties"])

    for key in (
        "response",
        "planUpdates",
        "sources",
        "ride_note_update",
        "ride_label_update",
        "physiologyRationale",
        "contextRationale",
    ):
        assert key in produced


def test_the_ordering_lists_every_property():
    """A property missing from the ordering is generated in an undefined place."""
    assert set(COACH_REPLY_SCHEMA["propertyOrdering"]) == set(
        COACH_REPLY_SCHEMA["properties"]
    )


# ---------------------------------------------------------------------------
# Plumbing: the schema has to reach the provider, on the coach call
# ---------------------------------------------------------------------------


PLAN = [{"date": "2026-04-16", "workoutType": "endurance", "durationMinutes": 60}]
PROFILE = {"name": "Test Rider", "currentFTP": 300}


@pytest.mark.asyncio
async def test_the_coach_call_is_constrained_by_the_schema():
    captured: dict = {}

    class FakeProvider:
        async def chat_history(self, system, messages, json_mode=False, response_schema=None):
            captured["json_mode"] = json_mode
            captured["schema"] = response_schema
            return json.dumps({"thinking": "…", "response": "Alles klar."})

    with patch.object(ai_service, "get_provider", return_value=FakeProvider()):
        result = await ai_service.ask_trainer(
            question="und morgen?", plan=PLAN, profile=PROFILE
        )

    assert result["response"] == "Alles klar."
    assert captured["json_mode"] is True
    assert captured["schema"] is COACH_REPLY_SCHEMA


def test_gemini_sends_the_schema_only_alongside_json_mode():
    """A schema without the JSON mime type is not a combination the API takes."""
    from google.genai import types

    from services.llm import GeminiProvider

    provider = GeminiProvider(model="gemini-3.5-flash-lite", api_key="fake")

    with_json = provider._build_config("s", True, types, COACH_REPLY_SCHEMA)
    without_json = provider._build_config("s", False, types, COACH_REPLY_SCHEMA)
    without_schema = provider._build_config("s", True, types)

    # Equality, not identity: pydantic copies the dict into the config model.
    assert with_json.response_schema == COACH_REPLY_SCHEMA
    assert without_json.response_schema is None
    assert without_schema.response_schema is None


def test_openai_ignores_the_schema_rather_than_failing_on_it():
    """The provider comes from the athlete's settings; the caller cannot know it."""
    import inspect

    from services.llm import OpenAIProvider

    for method in (OpenAIProvider.chat, OpenAIProvider.chat_history):
        assert "response_schema" in inspect.signature(method).parameters
