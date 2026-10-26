"""What the coach saw, under a chat reply (ai-trainer-ops#17, #81 Q24).

The deterministic blocks the coach was given, each with the function that
computes it. The prose stays unlinked; these are the numbers behind it.
"""

import inspect
from datetime import timedelta
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from config import settings as app_settings
from services import plan_context
from services.dates import app_today

REPO = Path(__file__).resolve().parents[2]


def test_every_section_links_to_the_function_that_computes_it():
    items = plan_context.coach_saw(
        [(key, f"text for {key}") for key in plan_context.SECTION_SOURCES], "abc123"
    )
    for item in items:
        source = item["source"]
        lines = (REPO / source["path"]).read_text().splitlines()
        assert lines[source["line"] - 1].startswith(f"def {source['symbol']}(")
        assert source["ref"] == "abc123"
        assert item["text"] == f"text for {item['key']}"


@settings(max_examples=50, deadline=None)
@given(
    present=st.lists(
        st.sampled_from(list(plan_context.SECTION_SOURCES)), unique=True, max_size=7
    )
)
def test_the_sections_are_the_coherence_block_piece_by_piece(present):
    """What the athlete reads is exactly what the prompt got, in order."""
    texts = {key: f"{key} says something" for key in present}
    today = app_today()
    exposure = plan_context.run_durability.run_exposure([], today)
    sections = plan_context._coherence_sections(
        [],
        today,
        exposure,
        None,
        phase=texts.get("phase", ""),
        goals=texts.get("goals", ""),
        priors=texts.get("priors", ""),
    )
    block = plan_context._coherence_block(
        [],
        today,
        exposure,
        None,
        phase=texts.get("phase", ""),
        goals=texts.get("goals", ""),
        priors=texts.get("priors", ""),
    )
    assert "\n\n".join(text for _key, text in sections) == block


@pytest.mark.asyncio
async def test_a_reply_says_what_the_coach_saw_with_the_deployed_commit(
    client, auth_headers, mock_ai_service, monkeypatch
):
    monkeypatch.setattr(app_settings, "git_sha", "deadbeef")
    await client.put("/api/v1/users/me", headers=auth_headers, json={"weeklyHours": 3})
    today = app_today()
    await client.put(
        "/api/v1/users/me/plan",
        headers=auth_headers,
        json={
            "plan": [
                {
                    "date": (today + timedelta(days=offset)).isoformat(),
                    "workoutType": "endurance",
                    "title": "Endurance",
                    "description": "Z2",
                    "durationMinutes": 60,
                }
                for offset in range(14)
            ]
        },
    )

    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "Wie sieht die Woche aus?"},
    )

    assert response.status_code == 200, response.text
    saw = {item["key"]: item for item in response.json()["coachSaw"]}
    assert "the athlete said they can give 3 h a week" in saw["priors"]["text"]
    assert saw["priors"]["source"] == {
        "path": "backend/services/plan_priors.py",
        "line": inspect.getsourcelines(plan_context.plan_priors.find_prior_departures)[
            1
        ],
        "symbol": "find_prior_departures",
        "ref": "deadbeef",
    }
