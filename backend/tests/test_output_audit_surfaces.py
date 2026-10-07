"""Every prose surface the athlete reads goes through the output audit (ai-trainer-ops#28).

``ask_trainer`` was wired in #750. This covers the rest of what the athlete
reads as the coach speaking: the dashboard login summary (from both analysis
paths, from the refresh and from processed ride feedback), the
training-status badge, the next-session recommendation, the ride review and
the generated insights.

Each case drives the real service function with the LLM replaced by a reply
that invents a number and asserts a condition, and checks the counters for its
surface. What is asserted is only that the audit *ran* on the text the athlete
would see, against the context the model was given — the checks themselves are
pinned in ``test_output_audit.py``.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from services import ai_service, metrics
from services.output_audit import CHECK_CLAIMS, CHECK_NUMBERS
from tests.test_ai_service_unit import _FakeRide
from tests.test_next_ride_recommendation import (
    PLAN_WITH_INTERVALS,
    PROFILE,
    FakeRideMetric,
)
from tests.test_training_status_coaching import FACTS

# An FTP nobody computed, and a diagnosis. Both checks must find something.
INVENTED = "Your FTP is 4817 W. Given the fatigue pattern, you have anemia."


def _counter(name: str, **labels) -> float:
    value = metrics.REGISTRY.get_sample_value(name, labels)
    return 0.0 if value is None else value


def _snapshot(surface: str) -> tuple[float, float, float]:
    return (
        _counter("coach_output_audits_total", surface=surface),
        _counter(
            "coach_output_audit_findings_total", surface=surface, check=CHECK_NUMBERS
        ),
        _counter(
            "coach_output_audit_findings_total", surface=surface, check=CHECK_CLAIMS
        ),
    )


async def _analyse_strava():
    return await ai_service.analyse_strava_activities(
        [
            {
                "id": 1,
                "name": "Ride",
                "type": "Ride",
                "start_date_local": "2026-07-20T08:00:00",
                "moving_time": 3600,
                "distance": 30000,
            }
        ],
        provider="openai",
    )


async def _analyse_fit():
    return await ai_service.analyse_fit_activity(
        sport_type="cycling", duration_minutes=90, avg_power=210, avg_hr=150
    )


async def _login_summary():
    return await ai_service.generate_login_summary(
        ride_insights="Solid aerobic base building.",
        last_ride_feedback=None,
        notes=None,
        estimated_ftp=250,
    )


async def _feedback_summary():
    return await ai_service.generate_summary_from_ride_feedbacks(
        [_FakeRide("2026-05-01")], provider="openai"
    )


async def _training_status():
    return await ai_service.generate_training_status(FACTS)


async def _ride_review():
    return await ai_service.batch_review_rides(
        [_FakeRide("2026-05-01")], profile={"name": "Alice"}, provider="openai"
    )


async def _next_session():
    return await ai_service.recommend_next_session(
        rides=[FakeRideMetric()], plan=PLAN_WITH_INTERVALS, profile=PROFILE
    )


async def _insights():
    return await ai_service.generate_athlete_insights(
        "Recent activity history (newest first):\n  2026-05-10 | endurance",
        provider="openai",
    )


_LOGIN_SUMMARY = f"{INVENTED}\n- Next session: easy spin on Saturday."

SURFACES = [
    pytest.param(
        "login_summary", _analyse_strava, {"loginSummary": _LOGIN_SUMMARY},
        id="strava-analysis",
    ),
    pytest.param(
        "login_summary", _analyse_fit, {"loginSummary": _LOGIN_SUMMARY},
        id="fit-analysis",
    ),
    pytest.param(
        "login_summary", _login_summary, {"loginSummary": _LOGIN_SUMMARY},
        id="login-summary-refresh",
    ),
    pytest.param(
        "login_summary", _feedback_summary, {"loginSummary": _LOGIN_SUMMARY},
        id="pending-feedbacks-summary",
    ),
    pytest.param(
        "training_status",
        _training_status,
        {"label": "On track", "tone": "steady", "rationale": INVENTED},
        id="training-status",
    ),
    pytest.param("ride_review", _ride_review, {"review": INVENTED}, id="ride-review"),
    pytest.param(
        "next_session",
        _next_session,
        {
            "response": "Good ride.",
            "next_session_recommendation": INVENTED,
            "recommendation_type": "keep_as_planned",
        },
        id="next-session",
    ),
    pytest.param(
        "insights",
        _insights,
        {"candidates": [{"fact": INVENTED, "category": "general", "confidence": 0.7}]},
        id="insights",
    ),
]


@pytest.mark.parametrize("surface, call, reply", SURFACES)
async def test_the_surface_is_audited(surface, call, reply):
    before = _snapshot(surface)

    with patch.object(ai_service, "_chat", new=AsyncMock(return_value=json.dumps(reply))):
        await call()

    audits, numbers, claims = _snapshot(surface)
    assert audits == before[0] + 1
    assert numbers > before[1], "the invented FTP was not traced"
    assert claims > before[2], "the diagnosis was not reported"


async def test_a_number_from_the_user_message_is_not_an_invention():
    """The context is the prompt *and* the user message, not the prompt alone.

    The training history lives in the user message for most of these surfaces;
    a context of the system prompt alone would report every number the coach
    correctly repeated from it.
    """
    before = _snapshot("insights")
    reply = {
        "candidates": [
            {"fact": "Holds 278 W on long climbs", "category": "general", "confidence": 0.7}
        ]
    }

    with patch.object(ai_service, "_chat", new=AsyncMock(return_value=json.dumps(reply))):
        await ai_service.generate_athlete_insights(
            "Recent activity history (newest first):\n  2026-05-10 | climb | 278 W",
            provider="openai",
        )

    audits, numbers, _ = _snapshot("insights")
    assert audits == before[0] + 1
    assert numbers == before[1]


async def test_output_the_athlete_never_sees_is_not_counted():
    """A rejected login summary is replaced by nothing, so it is not audited.

    Counting it would inflate the denominator that every finding is read
    against.
    """
    before = _snapshot("login_summary")
    reply = {"loginSummary": "You had a"}  # truncated: no bullet, too short

    with patch.object(ai_service, "_chat", new=AsyncMock(return_value=json.dumps(reply))):
        assert await _login_summary() == ""

    assert _snapshot("login_summary") == before


async def test_the_deterministic_fallback_summary_is_not_counted():
    """When the model's summary is unusable, code writes one from the rides.

    Nothing at the seam produced it, so there is nothing to audit.
    """
    before = _snapshot("login_summary")

    with patch.object(
        ai_service, "_chat", new=AsyncMock(return_value='{"loginSummary": ""}')
    ):
        assert await _feedback_summary()

    assert _snapshot("login_summary") == before


async def test_a_rejected_training_status_is_not_counted():
    before = _snapshot("training_status")
    reply = {"label": "", "tone": "steady", "rationale": INVENTED}

    with patch.object(ai_service, "_chat", new=AsyncMock(return_value=json.dumps(reply))):
        assert await _training_status() is None

    assert _snapshot("training_status") == before


async def test_the_audit_changes_nothing_the_caller_gets():
    """Report-only: the invented text is returned exactly as before."""
    with patch.object(
        ai_service, "_chat", new=AsyncMock(return_value=json.dumps({"review": INVENTED}))
    ):
        assert await _ride_review() == INVENTED
