"""The response schema the coach's Gemini call is constrained by (#558).

``response_mime_type: application/json`` is a request, not a guarantee: in
production the coach answered three questions in a row in plain German prose,
which nothing downstream could parse. A ``response_schema`` is the difference —
the API constrains generation to the shape instead of being asked nicely for
it, on any model. That matters more than the model choice: the same failure
predates the switch to Flash-Lite (#511), it was already guarded against in the
inquiry path while the coach still ran on Flash.

Two properties of this file are load-bearing.

**Nothing the plan pipeline can persist may be missing from it.** A field the
coach is allowed to write today but that this schema omits would stop reaching
the plan tomorrow, silently — the drift-bug class of #422 and #424, where the
coach promises a change in prose that never lands in the stored day. The
``planUpdates`` item therefore covers every field of
``schemas.PlanDayUpdateSchema``, the canonical shape the persist gate accepts,
and a test walks that model to prove it stays covered as it grows.

**The order is the reasoning order.** Gemini emits properties in
``propertyOrdering`` order, so ``thinking`` comes first: the prompt asks the
model to reason before it answers, and a schema that emitted ``response`` first
would quietly delete the chain of thought — a quality regression that no test
of the JSON shape would ever catch.

Only ``thinking`` and ``response`` are required. Everything else is a change
the coach may or may not want to make, and a schema that demanded them would
push the model into inventing plan edits nobody asked for.
"""

from __future__ import annotations

# {low, high} — the shape ``schemas._coerce_target_range`` normalises to. It
# accepts a bare scalar too, but a schema has to pick one spelling, and the
# range is the one the plan stores.
_RANGE = {
    "type": "OBJECT",
    "properties": {"low": {"type": "INTEGER"}, "high": {"type": "INTEGER"}},
    "required": ["low", "high"],
    "propertyOrdering": ["low", "high"],
}

# One interval rep. The prompt materialises every rep as its own object rather
# than a count, so that 4x2min is four entries; keep both in step.
_INTERVAL = {
    "type": "OBJECT",
    "properties": {
        "duration": {"type": "INTEGER", "description": "seconds"},
        "power": {"type": "INTEGER", "description": "watts"},
        "rest": {"type": "INTEGER", "description": "seconds"},
    },
    "required": ["duration", "power", "rest"],
    "propertyOrdering": ["duration", "power", "rest"],
}

# Mirrors schemas.PlanDayUpdateSchema field for field; see the module docstring.
_PLAN_UPDATE = {
    "type": "OBJECT",
    "properties": {
        "date": {"type": "STRING", "description": "ISO date of an existing plan day"},
        # Two-a-days: which session on that date this update targets (#496).
        "slot": {"type": "INTEGER"},
        "timeOfDay": {"type": "STRING"},
        "workoutType": {"type": "STRING"},
        "title": {"type": "STRING"},
        "description": {"type": "STRING"},
        "durationMinutes": {"type": "INTEGER"},
        # A planned window; when both are set durationMinutes is the midpoint (#368).
        "durationMinMinutes": {"type": "INTEGER"},
        "durationMaxMinutes": {"type": "INTEGER"},
        "targetPower": _RANGE,
        "targetHeartRate": _RANGE,
        "intervals": {"type": "ARRAY", "items": _INTERVAL},
        "workoutPurpose": {"type": "STRING"},
        "keyFocusPoints": {"type": "ARRAY", "items": {"type": "STRING"}},
        "completed": {"type": "BOOLEAN"},
    },
    "required": ["date"],
    "propertyOrdering": [
        "date",
        "slot",
        "timeOfDay",
        "workoutType",
        "title",
        "description",
        "durationMinutes",
        "durationMinMinutes",
        "durationMaxMinutes",
        "targetPower",
        "targetHeartRate",
        "intervals",
        "workoutPurpose",
        "keyFocusPoints",
        "completed",
    ],
}

# snake_case on purpose: these two keys are read straight off the parsed reply
# by ai_service, not through a CamelModel, and the prompt spells them this way.
_RIDE_NOTE_UPDATE = {
    "type": "OBJECT",
    "properties": {
        "activity_date": {"type": "STRING", "description": "YYYY-MM-DD"},
        "note": {"type": "STRING", "description": "1-2 sentence summary"},
    },
    "required": ["activity_date", "note"],
    "propertyOrdering": ["activity_date", "note"],
}

_RIDE_LABEL_UPDATE = {
    "type": "OBJECT",
    "properties": {
        "activity_date": {"type": "STRING", "description": "YYYY-MM-DD"},
        "label": {"type": "STRING", "description": "one of the labels named in the prompt"},
    },
    "required": ["activity_date", "label"],
    "propertyOrdering": ["activity_date", "label"],
}

# The arrangement a plan change is part of, when it spans more days than it
# edits (#667). A pin protects the day the coach wrote; this protects the days
# that day was written *for*.
_PLAN_COMMITMENT = {
    "type": "OBJECT",
    "properties": {
        "startDate": {"type": "STRING", "description": "YYYY-MM-DD, inclusive"},
        "endDate": {"type": "STRING", "description": "YYYY-MM-DD, inclusive"},
        "text": {
            "type": "STRING",
            "description": "the arrangement in one sentence, as the athlete would recognise it",
        },
    },
    "required": ["startDate", "endDate", "text"],
    "propertyOrdering": ["startDate", "endDate", "text"],
}

COACH_REPLY_SCHEMA: dict = {
    "type": "OBJECT",
    "properties": {
        "thinking": {"type": "STRING", "description": "internal reasoning, never shown"},
        "response": {"type": "STRING", "description": "the answer to the athlete"},
        "physiologyRationale": {"type": "STRING"},
        "contextRationale": {"type": "STRING"},
        # What the advice buys the athlete in terms of what THEY train for
        # (#565). Distinct from the two above: those say where a claim came
        # from, this says what it is for.
        "objectiveRationale": {"type": "STRING"},
        "sources": {"type": "ARRAY", "items": {"type": "STRING"}},
        "ride_note_update": _RIDE_NOTE_UPDATE,
        "ride_label_update": _RIDE_LABEL_UPDATE,
        "planUpdates": {"type": "ARRAY", "items": _PLAN_UPDATE},
        "planCommitment": _PLAN_COMMITMENT,
    },
    "required": ["thinking", "response"],
    "propertyOrdering": [
        "thinking",
        "response",
        "physiologyRationale",
        "contextRationale",
        "objectiveRationale",
        "sources",
        "ride_note_update",
        "ride_label_update",
        "planUpdates",
        "planCommitment",
    ],
}
