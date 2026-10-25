"""The athlete-model writers: insights, hypotheses, experiments, predictions, contradictions, observations.

Split out of ``test_ai_service_unit.py`` (ai-trainer-ops#259); its history is there.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

import services.ai_service as ai_service


@pytest.mark.asyncio
async def test_match_observations_to_recommendations_routes_by_llm():
    """LLM matcher routes each observation to its assigned recommendation index."""
    recs = [
        {"recommendation": "Prioritise recovery rides."},
        {"recommendation": "Add one long endurance ride."},
    ]
    observations = [
        "Athlete overreaches when feeling fresh.",
        "Tends to skip long rides.",
    ]

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        return json.dumps(
            {
                "assignments": [
                    {"observationIndex": 0, "recommendationIndex": 0},
                    {"observationIndex": 1, "recommendationIndex": 1},
                ]
            }
        )

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        result = await ai_service.match_observations_to_recommendations(
            recs, observations, provider="openai"
        )

    assert result == {
        0: ["Athlete overreaches when feeling fresh."],
        1: ["Tends to skip long rides."],
    }


@pytest.mark.asyncio
async def test_match_observations_omitted_or_null_falls_back_to_primary():
    """Observations the model omits or marks irrelevant land on the primary rec."""
    recs = [{"recommendation": "A"}, {"recommendation": "B"}]
    observations = ["relevant to B", "irrelevant", "never mentioned"]

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        return json.dumps(
            {
                "assignments": [
                    {"observationIndex": 0, "recommendationIndex": 1},
                    {"observationIndex": 1, "recommendationIndex": None},
                    # observationIndex 2 omitted entirely
                ]
            }
        )

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        result = await ai_service.match_observations_to_recommendations(
            recs, observations, provider="openai"
        )

    assert result[1] == ["relevant to B"]
    # Irrelevant + omitted both fall back to the primary recommendation, in order.
    assert result[0] == ["irrelevant", "never mentioned"]


@pytest.mark.asyncio
async def test_match_observations_empty_inputs_skip_llm():
    """No observations or recommendations → no LLM call, empty assignments."""
    with patch.object(ai_service, "_chat", side_effect=AssertionError("should not call")):
        assert await ai_service.match_observations_to_recommendations([], ["x"]) == {}
        assert (
            await ai_service.match_observations_to_recommendations([{"recommendation": "A"}], [])
            == {}
        )


# ---------------------------------------------------------------------------
# generate_athlete_insights — infer durable patterns from training history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_athlete_insights_returns_normalised_candidates():
    captured: list[tuple[str, str]] = []

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        captured.append((system_prompt, user_msg))
        return json.dumps({
            "candidates": [
                {
                    "fact": "Performs best after one recovery day",
                    "kind": "observation",
                    "category": "fatigue_response",
                    "confidence": 0.6,
                    "sourceSnippet": "Strong sessions followed rest days on 05-03 and 05-10.",
                },
                {
                    # Confidence above the cap is clamped to 0.9.
                    "fact": "Estimated FTP around 250 W",
                    "kind": "fact",
                    "category": "general",
                    "confidence": 1.4,
                },
                {
                    # A missing kind falls back to the safer "observation".
                    "fact": "Tolerates heat well",
                    "category": "general",
                    "confidence": 0.5,
                },
            ]
        })

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        candidates = await ai_service.generate_athlete_insights(
            "Recent activity history (newest first):\n  2026-05-10 | ...",
            existing_facts=["Prefers morning rides"],
            provider="openai",
        )

    assert [c["fact"] for c in candidates] == [
        "Performs best after one recovery day",
        "Estimated FTP around 250 W",
        "Tolerates heat well",
    ]
    assert [c["kind"] for c in candidates] == ["observation", "fact", "observation"]
    assert candidates[1]["confidence"] == 0.9
    # History and the existing-facts guard both reach the model.
    assert "2026-05-10" in captured[0][1]
    assert "Prefers morning rides" in captured[0][1]


@pytest.mark.asyncio
async def test_generate_athlete_insights_empty_history_skips_model():
    called = False

    async def fake_chat(*args, **kwargs):
        nonlocal called
        called = True
        return "{}"

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        candidates = await ai_service.generate_athlete_insights("   ", provider="openai")

    assert candidates == []
    assert called is False


@pytest.mark.asyncio
async def test_generate_athlete_insights_dedupes_and_caps():
    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        candidates = [
            {"fact": "Fades late in long rides", "category": "recurring_issues", "confidence": 0.5},
            {"fact": " fades LATE in long rides ", "category": "recurring_issues", "confidence": 0.7},
        ]
        candidates += [
            {"fact": f"Pattern {i}", "category": "general", "confidence": 0.4}
            for i in range(ai_service.MAX_GENERATED_INSIGHTS + 5)
        ]
        return json.dumps({"candidates": candidates})

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        candidates = await ai_service.generate_athlete_insights(
            "history", provider="openai"
        )

    facts = [c["fact"] for c in candidates]
    assert facts.count("Fades late in long rides") == 1
    assert len(candidates) == ai_service.MAX_GENERATED_INSIGHTS


@pytest.mark.asyncio
async def test_generate_athlete_insights_handles_malformed_payload():
    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        return json.dumps({"unexpected": "shape"})

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        candidates = await ai_service.generate_athlete_insights("history", provider="openai")

    assert candidates == []


# ---------------------------------------------------------------------------
# generate_athlete_hypotheses — form explicit, testable ideas from history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_athlete_hypotheses_returns_normalised_candidates():
    captured: list[tuple[str, str]] = []

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        captured.append((system_prompt, user_msg))
        return json.dumps({
            "candidates": [
                {
                    "statement": "Upper-body strength suppresses next-day HR response",
                    "category": "fatigue_response",
                    "confidence": 0.38,
                    "rationale": "HR ~8 bpm low the day after gym on 05-03 and 05-10.",
                },
                {
                    # Confidence above the hypothesis cap is clamped to 0.6.
                    "statement": "Rides stronger in the second half of a block",
                    "category": "general",
                    "confidence": 0.95,
                },
            ]
        })

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        candidates = await ai_service.generate_athlete_hypotheses(
            "Recent activity history (newest first):\n  2026-05-10 | ...",
            existing_facts=["Prefers morning rides"],
            existing_hypotheses=["Fuels poorly on long rides"],
            provider="openai",
        )

    assert [c["statement"] for c in candidates] == [
        "Upper-body strength suppresses next-day HR response",
        "Rides stronger in the second half of a block",
    ]
    assert candidates[0]["confidence"] == 0.38
    assert candidates[1]["confidence"] == 0.6
    # History, existing facts, and existing hypotheses all reach the model.
    assert "2026-05-10" in captured[0][1]
    assert "Prefers morning rides" in captured[0][1]
    assert "Fuels poorly on long rides" in captured[0][1]


@pytest.mark.asyncio
async def test_generate_athlete_hypotheses_empty_history_skips_model():
    called = False

    async def fake_chat(*args, **kwargs):
        nonlocal called
        called = True
        return "{}"

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        candidates = await ai_service.generate_athlete_hypotheses(
            "   ", provider="openai"
        )

    assert candidates == []
    assert called is False


@pytest.mark.asyncio
async def test_generate_athlete_hypotheses_dedupes_and_caps():
    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        candidates = [
            {"statement": "Fades late in long rides", "category": "recurring_issues", "confidence": 0.4},
            {"statement": " fades LATE in long rides ", "category": "recurring_issues", "confidence": 0.5},
        ]
        candidates += [
            {"statement": f"Idea {i}", "category": "general", "confidence": 0.3}
            for i in range(ai_service.MAX_GENERATED_HYPOTHESES + 5)
        ]
        return json.dumps({"candidates": candidates})

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        candidates = await ai_service.generate_athlete_hypotheses(
            "history", provider="openai"
        )

    statements = [c["statement"] for c in candidates]
    assert statements.count("Fades late in long rides") == 1
    assert len(candidates) == ai_service.MAX_GENERATED_HYPOTHESES


@pytest.mark.asyncio
async def test_generate_athlete_hypotheses_handles_malformed_payload():
    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        return json.dumps({"unexpected": "shape"})

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        candidates = await ai_service.generate_athlete_hypotheses(
            "history", provider="openai"
        )

    assert candidates == []


# ---------------------------------------------------------------------------
# generate_validation_experiments — propose experiments to resolve uncertainty
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_validation_experiments_returns_normalised_candidates():
    captured: list[tuple[str, str]] = []

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        captured.append((system_prompt, user_msg))
        return json.dumps({
            "candidates": [
                {
                    "question": "Does upper-body strength suppress next-day HR?",
                    "protocol": "Repeat the gym session, compare HR next easy ride.",
                    "rationale": "A clear HR drop confirms the hypothesis.",
                    "category": "fatigue_response",
                },
                {
                    # Missing rationale defaults to empty; category defaults.
                    "question": "Which bike is faster?",
                    "protocol": "Compare both bikes over the same climb.",
                },
            ]
        })

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        candidates = await ai_service.generate_validation_experiments(
            "Open questions:\n- upper-body strength suppresses next-day HR",
            existing_experiments=["Perform a 30-minute threshold test."],
            provider="openai",
        )

    assert [c["protocol"] for c in candidates] == [
        "Repeat the gym session, compare HR next easy ride.",
        "Compare both bikes over the same climb.",
    ]
    assert candidates[1]["rationale"] == ""
    assert candidates[1]["category"] == "general"
    # Uncertainties and already-suggested experiments both reach the model.
    assert "upper-body strength" in captured[0][1]
    assert "Perform a 30-minute threshold test." in captured[0][1]


@pytest.mark.asyncio
async def test_generate_validation_experiments_empty_context_skips_model():
    called = False

    async def fake_chat(*args, **kwargs):
        nonlocal called
        called = True
        return "{}"

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        candidates = await ai_service.generate_validation_experiments(
            "   ", provider="openai"
        )

    assert candidates == []
    assert called is False


@pytest.mark.asyncio
async def test_generate_validation_experiments_dedupes_and_caps():
    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        candidates = [
            {"question": "q", "protocol": "Repeat the VO2 session", "category": "general"},
            {"question": "q", "protocol": " repeat THE vo2 session ", "category": "general"},
        ]
        candidates += [
            {"question": "q", "protocol": f"Experiment {i}", "category": "general"}
            for i in range(ai_service.MAX_GENERATED_EXPERIMENTS + 5)
        ]
        return json.dumps({"candidates": candidates})

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        candidates = await ai_service.generate_validation_experiments(
            "uncertainty", provider="openai"
        )

    protocols = [c["protocol"] for c in candidates]
    assert protocols.count("Repeat the VO2 session") == 1
    assert len(candidates) == ai_service.MAX_GENERATED_EXPERIMENTS


@pytest.mark.asyncio
async def test_generate_validation_experiments_handles_malformed_payload():
    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        return json.dumps({"unexpected": "shape"})

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        candidates = await ai_service.generate_validation_experiments(
            "uncertainty", provider="openai"
        )

    assert candidates == []


# ---------------------------------------------------------------------------
# generate_athlete_predictions — make checkable, forward-looking predictions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_athlete_predictions_returns_normalised_candidates():
    captured: list[tuple[str, str]] = []

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        captured.append((system_prompt, user_msg))
        return json.dumps({
            "candidates": [
                {
                    "prediction": "The athlete will be fully recovered tomorrow",
                    "expectedOutcome": "Resting HR back to baseline, ready for intensity",
                    "horizon": "tomorrow",
                    "confidence": 0.7,
                    "category": "fatigue_response",
                },
                {
                    # Missing horizon defaults to empty; confidence clamps to 1.0.
                    "prediction": "Fatigue forces an easier week within 10 days",
                    "expectedOutcome": "A drop in weekly TSS",
                    "confidence": 1.4,
                },
            ]
        })

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        candidates = await ai_service.generate_athlete_predictions(
            "Recent activity history (newest first):\n  2026-05-10 | ...",
            existing_predictions=["The athlete will PR the local climb"],
            provider="openai",
        )

    assert [c["prediction"] for c in candidates] == [
        "The athlete will be fully recovered tomorrow",
        "Fatigue forces an easier week within 10 days",
    ]
    assert candidates[0]["horizon"] == "tomorrow"
    assert candidates[1]["horizon"] == ""
    assert candidates[1]["confidence"] == 1.0
    # History and existing predictions both reach the model.
    assert "2026-05-10" in captured[0][1]
    assert "PR the local climb" in captured[0][1]


@pytest.mark.asyncio
async def test_generate_athlete_predictions_requires_expected_outcome():
    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        return json.dumps({
            "candidates": [
                {"prediction": "Something vague", "confidence": 0.5},
                {
                    "prediction": "Will hit interval targets",
                    "expectedOutcome": "Completes all reps at target power",
                    "confidence": 0.6,
                },
            ]
        })

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        candidates = await ai_service.generate_athlete_predictions(
            "history", provider="openai"
        )

    # The candidate without an expected outcome cannot be checked and is dropped.
    assert [c["prediction"] for c in candidates] == ["Will hit interval targets"]


@pytest.mark.asyncio
async def test_generate_athlete_predictions_empty_history_skips_model():
    called = False

    async def fake_chat(*args, **kwargs):
        nonlocal called
        called = True
        return "{}"

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        candidates = await ai_service.generate_athlete_predictions(
            "   ", provider="openai"
        )

    assert candidates == []
    assert called is False


@pytest.mark.asyncio
async def test_generate_athlete_predictions_dedupes_and_caps():
    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        candidates = [
            {"prediction": "Recovers by tomorrow", "expectedOutcome": "HR baseline", "confidence": 0.6},
            {"prediction": " recovers BY tomorrow ", "expectedOutcome": "HR baseline", "confidence": 0.7},
        ]
        candidates += [
            {"prediction": f"Prediction {i}", "expectedOutcome": "outcome", "confidence": 0.5}
            for i in range(ai_service.MAX_GENERATED_PREDICTIONS + 5)
        ]
        return json.dumps({"candidates": candidates})

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        candidates = await ai_service.generate_athlete_predictions(
            "history", provider="openai"
        )

    predictions = [c["prediction"] for c in candidates]
    assert predictions.count("Recovers by tomorrow") == 1
    assert len(candidates) == ai_service.MAX_GENERATED_PREDICTIONS


@pytest.mark.asyncio
async def test_generate_athlete_predictions_handles_malformed_payload():
    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        return json.dumps({"unexpected": "shape"})

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        candidates = await ai_service.generate_athlete_predictions(
            "history", provider="openai"
        )

    assert candidates == []


# ---------------------------------------------------------------------------
# evaluate_athlete_predictions — score pending predictions against the data
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_athlete_predictions_returns_scored_verdicts():
    captured: list[tuple[str, str]] = []

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        captured.append((system_prompt, user_msg))
        return json.dumps({
            "evaluations": [
                {"index": 0, "verdict": "correct", "actualOutcome": "HR back to baseline on 05-11"},
                {"index": 1, "verdict": "incorrect", "actualOutcome": "Still fatigued, missed targets"},
                # 'unknown' verdicts are dropped so the prediction stays pending.
                {"index": 2, "verdict": "unknown", "actualOutcome": "Too early to tell"},
            ]
        })

    predictions = [
        {"prediction": "Recovered tomorrow", "expected_outcome": "HR baseline", "horizon": "tomorrow"},
        {"prediction": "Hits VO2 targets", "expected_outcome": "All reps at power", "horizon": "next ride"},
        {"prediction": "Easier week soon", "expected_outcome": "TSS drop", "horizon": "2 weeks"},
    ]

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        evaluations = await ai_service.evaluate_athlete_predictions(
            "Recent activity history:\n  2026-05-11 | ...",
            predictions,
            provider="openai",
        )

    assert evaluations == [
        {"index": 0, "correct": True, "actual_outcome": "HR back to baseline on 05-11"},
        {"index": 1, "correct": False, "actual_outcome": "Still fatigued, missed targets"},
    ]
    # The predictions and their expected outcomes reach the model.
    assert "Recovered tomorrow" in captured[0][1]
    assert "HR baseline" in captured[0][1]


@pytest.mark.asyncio
async def test_evaluate_athlete_predictions_no_predictions_skips_model():
    called = False

    async def fake_chat(*args, **kwargs):
        nonlocal called
        called = True
        return "{}"

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        evaluations = await ai_service.evaluate_athlete_predictions(
            "history", [], provider="openai"
        )

    assert evaluations == []
    assert called is False


@pytest.mark.asyncio
async def test_evaluate_athlete_predictions_drops_out_of_range_and_dupes():
    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        return json.dumps({
            "evaluations": [
                {"index": 0, "verdict": "correct", "actualOutcome": "ok"},
                {"index": 0, "verdict": "incorrect", "actualOutcome": "dupe ignored"},
                {"index": 9, "verdict": "correct", "actualOutcome": "out of range"},
            ]
        })

    predictions = [
        {"prediction": "p0", "expected_outcome": "o0", "horizon": ""},
    ]

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        evaluations = await ai_service.evaluate_athlete_predictions(
            "history", predictions, provider="openai"
        )

    assert evaluations == [{"index": 0, "correct": True, "actual_outcome": "ok"}]


@pytest.mark.asyncio
async def test_evaluate_athlete_predictions_handles_malformed_payload():
    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        return json.dumps({"unexpected": "shape"})

    predictions = [{"prediction": "p0", "expected_outcome": "o0", "horizon": ""}]

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        evaluations = await ai_service.evaluate_athlete_predictions(
            "history", predictions, provider="openai"
        )

    assert evaluations == []


# ---------------------------------------------------------------------------
# detect_athlete_fact_contradictions — flag stored facts fresh data disagrees with
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detect_contradictions_returns_valid_indexed_reasons():
    captured: list[tuple[str, str]] = []

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        captured.append((system_prompt, user_msg))
        return json.dumps({
            "contradictions": [
                {"factIndex": 0, "reason": "Held 400 W for 5x4 min — above stored 320 W FTP."},
            ]
        })

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        contradictions = await ai_service.detect_athlete_fact_contradictions(
            "Recent activity history:\n  2026-07-05 | 5x4 min @ 400 W",
            ["FTP is around 320 W", "Prefers morning rides"],
            provider="openai",
        )

    assert contradictions == [
        {"factIndex": 0, "reason": "Held 400 W for 5x4 min — above stored 320 W FTP."}
    ]
    # Both the history and the numbered facts reach the model.
    assert "400 W" in captured[0][1]
    assert "0. FTP is around 320 W" in captured[0][1]


@pytest.mark.asyncio
async def test_detect_contradictions_drops_out_of_range_and_blank():
    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        return json.dumps({
            "contradictions": [
                {"factIndex": 5, "reason": "out of range"},
                {"factIndex": 0, "reason": "  "},
                {"factIndex": 1, "reason": "Contradicted by the data."},
                {"factIndex": 1, "reason": "duplicate index, ignored"},
            ]
        })

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        contradictions = await ai_service.detect_athlete_fact_contradictions(
            "history", ["fact a", "fact b"], provider="openai"
        )

    assert contradictions == [{"factIndex": 1, "reason": "Contradicted by the data."}]


@pytest.mark.asyncio
async def test_detect_contradictions_skips_model_without_facts_or_history():
    called = False

    async def fake_chat(*args, **kwargs):
        nonlocal called
        called = True
        return "{}"

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        assert await ai_service.detect_athlete_fact_contradictions(
            "history", [], provider="openai"
        ) == []
        assert await ai_service.detect_athlete_fact_contradictions(
            "   ", ["a fact"], provider="openai"
        ) == []

    assert called is False


@pytest.mark.asyncio
async def test_detect_contradictions_handles_malformed_payload():
    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        return json.dumps({"unexpected": "shape"})

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        contradictions = await ai_service.detect_athlete_fact_contradictions(
            "history", ["a fact"], provider="openai"
        )

    assert contradictions == []
