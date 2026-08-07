"""The athlete motivation model — objectives, constraints, weights (#562).

The coaching system optimizes physiology; most athletes optimize something else
that physiology only enables. These tests pin the data layer that records the
difference: the normalization gate in :mod:`services.motivation_model`, and the
clobber-safe persistence in :mod:`crud`.

Two invariants get the most attention because both break silently:

* the weight vector spans exactly ``MOTIVATION_COMPONENTS`` and sums to 1.0, so
  a utility score is comparable at all (#564);
* ``user_set`` beats ``inferred``, so an inference pass (#563) can never
  overwrite an objective the athlete stated by hand — the stale-snapshot clobber
  class of #342/#345/#346.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
from services import motivation_model as mm
from tests.conftest import TestSessionLocal


NOW = datetime(2026, 8, 11, 9, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Weights
# ---------------------------------------------------------------------------


def test_weights_span_the_components_and_sum_to_one():
    weights = mm.normalize_weights({"enjoyment": 0.9, "adaptation": 0.9})

    assert set(weights) == set(mm.MOTIVATION_COMPONENTS)
    assert sum(weights.values()) == pytest.approx(1.0)


def test_missing_components_fall_back_to_defaults():
    """A partial vector from an inference pass is valid input, not an error."""
    weights = mm.normalize_weights({"enjoyment": 0.5})

    assert weights["race_performance"] > 0
    assert sum(weights.values()) == pytest.approx(1.0)


def test_unknown_components_are_dropped():
    weights = mm.normalize_weights({"vibes": 0.9, "enjoyment": 0.1})

    assert "vibes" not in weights
    assert sum(weights.values()) == pytest.approx(1.0)


def test_empty_vector_is_the_cold_start_default():
    assert mm.normalize_weights(None) == mm.DEFAULT_WEIGHTS
    assert sum(mm.DEFAULT_WEIGHTS.values()) == pytest.approx(1.0)


def test_pinned_weight_keeps_its_exact_value():
    """The athlete fixed this axis by hand (#567); normalization must not move it."""
    weights = mm.normalize_weights(
        {"enjoyment": 0.45, "adaptation": 0.9, "consistency": 0.9},
        pinned=["enjoyment"],
    )

    assert weights["enjoyment"] == pytest.approx(0.45)
    assert sum(weights.values()) == pytest.approx(1.0)


def test_pinning_everything_still_yields_a_valid_vector():
    weights = mm.normalize_weights(
        {c: 0.5 for c in mm.MOTIVATION_COMPONENTS},
        pinned=list(mm.MOTIVATION_COMPONENTS),
    )

    assert sum(weights.values()) == pytest.approx(1.0)


def test_all_zero_unpinned_weights_split_the_remainder_evenly():
    """Otherwise the vector would sum to the pinned mass alone."""
    raw = {c: 0.0 for c in mm.MOTIVATION_COMPONENTS}
    raw["health"] = 0.2
    weights = mm.normalize_weights(raw, pinned=["health"])

    assert weights["health"] == pytest.approx(0.2)
    assert sum(weights.values()) == pytest.approx(1.0)
    unpinned = [c for c in mm.MOTIVATION_COMPONENTS if c != "health"]
    assert len({round(weights[c], 4) for c in unpinned}) == 1


def test_negative_and_nonsense_weights_are_clamped():
    weights = mm.normalize_weights(
        {"enjoyment": -3.0, "adaptation": "lots", "health": 12.0}
    )

    assert all(0.0 <= value <= 1.0 for value in weights.values())
    assert sum(weights.values()) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Entries
# ---------------------------------------------------------------------------


def test_bare_string_becomes_a_full_entry():
    """Inference callers hand up text; the envelope is the gate's job."""
    entry = mm.normalize_entry("stay healthy", now=NOW)

    assert entry["text"] == "stay healthy"
    assert entry["source"] == mm.SOURCE_INFERRED
    assert entry["status"] == mm.STATUS_ACTIVE
    assert entry["first_observed_at"] == NOW.isoformat()


def test_entry_without_text_is_dropped():
    assert mm.normalize_entry({"confidence": 0.9}, now=NOW) is None
    assert mm.normalize_entry({"text": "   "}, now=NOW) is None


def test_user_set_entry_defaults_to_full_confidence():
    entry = mm.normalize_entry({"text": "finish the Alps traverse", "source": "user_set"})

    assert entry["confidence"] == pytest.approx(1.0)


def test_entries_are_deduplicated_and_capped():
    raw = ["ride trails"] * 3 + [f"objective {i}" for i in range(10)]
    entries = mm.normalize_entries(raw, limit=mm.MAX_SECONDARY_OBJECTIVES, now=NOW)

    assert len(entries) == mm.MAX_SECONDARY_OBJECTIVES
    assert len({e["text"] for e in entries}) == len(entries)


def test_the_cap_drops_the_weakest_evidence_not_the_newest():
    raw = [{"text": "weak", "confidence": 0.1}] + [
        {"text": f"strong {i}", "confidence": 0.9} for i in range(mm.MAX_CONSTRAINTS)
    ]
    entries = mm.normalize_entries(raw, limit=mm.MAX_CONSTRAINTS, now=NOW)

    assert "weak" not in {e["text"] for e in entries}


def test_user_set_entries_survive_the_cap_ahead_of_inferred_ones():
    """The cap drops guesses before it drops anything the athlete said."""
    raw = [{"text": f"guess {i}", "confidence": 0.99} for i in range(mm.MAX_CONSTRAINTS)]
    raw.append({"text": "stated by the athlete", "source": "user_set"})
    entries = mm.normalize_entries(raw, limit=mm.MAX_CONSTRAINTS, now=NOW)

    assert "stated by the athlete" in {e["text"] for e in entries}
    assert len(entries) == mm.MAX_CONSTRAINTS


def test_the_order_the_entries_arrive_in_is_the_order_they_keep():
    """For a user_set write that order is the athlete's own (#567).

    Ranking decides what the cap drops and nothing else; sorting the output
    would silently undo the sequence the athlete put their objectives in.
    """
    entries = mm.normalize_entries(
        [
            {"text": "inferred one", "confidence": 0.4},
            {"text": "stated", "source": "user_set"},
            {"text": "inferred two", "confidence": 0.9},
        ],
        limit=mm.MAX_SECONDARY_OBJECTIVES,
        now=NOW,
    )

    assert [e["text"] for e in entries] == ["inferred one", "stated", "inferred two"]


def test_an_athlete_reordering_their_objectives_is_stored_in_that_order():
    stored = mm.normalize_model(
        {
            "secondary_objectives": [
                {"text": "climb faster", "source": "user_set"},
                {"text": "ride the Trans-Alp", "source": "user_set"},
            ]
        }
    )

    merged = mm.merge_model(
        stored,
        {
            "secondary_objectives": [
                {"text": "ride the Trans-Alp", "source": "user_set"},
                {"text": "climb faster", "source": "user_set"},
            ]
        },
        source=mm.SOURCE_USER_SET,
    )

    assert [e["text"] for e in merged["secondary_objectives"]] == [
        "ride the Trans-Alp",
        "climb faster",
    ]


def test_retired_entries_are_dropped():
    entries = mm.normalize_entries(
        [{"text": "old goal", "status": "retired"}, {"text": "current goal"}],
        limit=mm.MAX_SECONDARY_OBJECTIVES,
        now=NOW,
    )

    assert [e["text"] for e in entries] == ["current goal"]


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def test_default_model_is_complete():
    """Every consumer gets the full shape, so none has to special-case absence."""
    model = mm.default_model()

    assert model["primary_objective"] == ""
    assert model["utility_weights"] == mm.DEFAULT_WEIGHTS
    assert model["secondary_objectives"] == []
    assert model["constraints"] == []
    assert model["pinned_weights"] == []


def test_garbage_input_normalizes_instead_of_raising():
    model = mm.normalize_model(
        {
            "primary_objective": 42,
            "secondary_objectives": "not a list",
            "constraints": None,
            "utility_weights": "nope",
            "pinned_weights": ["enjoyment", "made_up"],
        }
    )

    assert model["primary_objective"] == ""
    assert model["secondary_objectives"] == []
    assert model["pinned_weights"] == ["enjoyment"]
    assert sum(model["utility_weights"].values()) == pytest.approx(1.0)


def test_absent_primary_objective_carries_no_confidence():
    """Confidence on an empty string would describe nothing."""
    model = mm.normalize_model(
        {"primary_objective": "", "primary_objective_confidence": 0.9}
    )

    assert model["primary_objective_confidence"] == 0.0


# ---------------------------------------------------------------------------
# Merge: the user-set override
# ---------------------------------------------------------------------------


def test_inferred_write_does_not_clobber_a_user_set_objective():
    """The athlete said what they train for; #563 must not overwrite it."""
    stored = mm.normalize_model(
        {
            "primary_objective": "Maximize enjoyable technical trail riding",
            "primary_objective_source": "user_set",
        }
    )

    merged = mm.merge_model(
        stored,
        {"primary_objective": "Increase FTP", "primary_objective_confidence": 0.9},
        source=mm.SOURCE_INFERRED,
    )

    assert merged["primary_objective"] == "Maximize enjoyable technical trail riding"
    assert merged["primary_objective_source"] == mm.SOURCE_USER_SET


def test_user_set_write_always_wins():
    stored = mm.normalize_model(
        {"primary_objective": "Increase FTP", "primary_objective_source": "inferred"}
    )

    merged = mm.merge_model(
        stored,
        {"primary_objective": "Ride the Trans-Alp"},
        source=mm.SOURCE_USER_SET,
    )

    assert merged["primary_objective"] == "Ride the Trans-Alp"
    assert merged["primary_objective_source"] == mm.SOURCE_USER_SET
    assert merged["primary_objective_confidence"] == pytest.approx(1.0)


def test_inferred_write_fills_an_empty_objective():
    merged = mm.merge_model(
        mm.default_model(),
        {"primary_objective": "Enjoy long days outdoors", "primary_objective_confidence": 0.4},
        source=mm.SOURCE_INFERRED,
    )

    assert merged["primary_objective"] == "Enjoy long days outdoors"
    assert merged["primary_objective_confidence"] == pytest.approx(0.4)


def test_inferred_write_never_moves_a_pinned_weight():
    """Learning (#566) does not get a vote on an axis the athlete fixed."""
    stored = mm.normalize_model(
        {
            "utility_weights": {"enjoyment": 0.45},
            "pinned_weights": ["enjoyment"],
        }
    )
    pinned_before = stored["utility_weights"]["enjoyment"]

    merged = mm.merge_model(
        stored,
        {"utility_weights": {c: 0.2 for c in mm.MOTIVATION_COMPONENTS}},
        source=mm.SOURCE_INFERRED,
    )

    assert merged["utility_weights"]["enjoyment"] == pytest.approx(pinned_before)
    assert sum(merged["utility_weights"].values()) == pytest.approx(1.0)


def test_inferred_write_moves_unpinned_weights():
    merged = mm.merge_model(
        mm.default_model(),
        {"utility_weights": {"enjoyment": 0.8, "adaptation": 0.05}},
        source=mm.SOURCE_INFERRED,
    )

    assert merged["utility_weights"]["enjoyment"] > mm.DEFAULT_WEIGHTS["enjoyment"]
    assert sum(merged["utility_weights"].values()) == pytest.approx(1.0)


def test_inferred_write_keeps_a_user_set_entry_intact():
    stored = mm.normalize_model(
        {
            "secondary_objectives": [
                {"text": "Complete marathon races", "source": "user_set"}
            ]
        }
    )

    merged = mm.merge_model(
        stored,
        {"secondary_objectives": [{"text": "complete marathon races", "confidence": 0.2}]},
        source=mm.SOURCE_INFERRED,
        now=NOW,
    )

    entry = merged["secondary_objectives"][0]
    assert entry["source"] == mm.SOURCE_USER_SET
    assert entry["confidence"] == pytest.approx(1.0)
    # Re-observing it is confirmation, so recency still moves.
    assert entry["last_confirmed_at"] == NOW.isoformat()


def test_reobserving_an_inferred_entry_keeps_its_first_sighting():
    """#566 needs to tell a long-standing objective from one stated once."""
    earlier = (NOW - timedelta(days=90)).isoformat()
    stored = mm.normalize_model(
        {
            "secondary_objectives": [
                {"text": "more trail time", "confidence": 0.3, "first_observed_at": earlier}
            ]
        }
    )

    merged = mm.merge_model(
        stored,
        {"secondary_objectives": [{"text": "more trail time", "confidence": 0.7}]},
        source=mm.SOURCE_INFERRED,
        now=NOW,
    )

    entry = merged["secondary_objectives"][0]
    assert entry["first_observed_at"] == earlier
    assert entry["confidence"] == pytest.approx(0.7)


def test_an_entry_the_athlete_writes_is_attributed_to_them():
    """Correctness must not depend on the client sending the field (#567).

    Had this defaulted to ``inferred``, the next inference pass would have been
    free to overwrite what the athlete just typed — exactly the promise the
    settings screen makes.
    """
    merged = mm.merge_model(
        mm.default_model(),
        {"constraints": [{"text": "No racing, ever"}, "Stay healthy"]},
        source=mm.SOURCE_USER_SET,
    )

    assert all(e["source"] == mm.SOURCE_USER_SET for e in merged["constraints"])


def test_an_untouched_inferred_entry_keeps_its_provenance_through_a_user_write():
    """The UI shows it was a guess and what backed it; saving something else
    must not quietly promote it to fact."""
    merged = mm.merge_model(
        mm.default_model(),
        {
            "secondary_objectives": [
                {"text": "ride more trails", "source": "inferred", "confidence": 0.4}
            ]
        },
        source=mm.SOURCE_USER_SET,
    )

    entry = merged["secondary_objectives"][0]
    assert entry["source"] == mm.SOURCE_INFERRED
    assert entry["confidence"] == pytest.approx(0.4)


def test_a_user_set_edit_replaces_the_list_including_by_omission():
    stored = mm.normalize_model(
        {"constraints": [{"text": "avoid crash risk"}, {"text": "stay healthy"}]}
    )

    merged = mm.merge_model(
        stored, {"constraints": [{"text": "stay healthy"}]}, source=mm.SOURCE_USER_SET
    )

    assert [e["text"] for e in merged["constraints"]] == ["stay healthy"]


def test_a_partial_user_edit_leaves_untouched_fields_alone():
    """An omitted field is "nothing to say", not "delete this" (#567)."""
    stored = mm.normalize_model(
        {
            "primary_objective": "Ride more technical trails",
            "secondary_objectives": [{"text": "climb faster", "source": "user_set"}],
            "constraints": [{"text": "stay healthy", "source": "user_set"}],
            "utility_weights": {"enjoyment": 0.5},
        }
    )

    merged = mm.merge_model(
        stored, {"primary_objective": "Ride the Trans-Alp"}, source=mm.SOURCE_USER_SET
    )

    assert merged["primary_objective"] == "Ride the Trans-Alp"
    assert [e["text"] for e in merged["secondary_objectives"]] == ["climb faster"]
    assert [e["text"] for e in merged["constraints"]] == ["stay healthy"]
    assert merged["utility_weights"] == stored["utility_weights"]


def test_an_athlete_may_clear_their_objective_but_inference_may_not():
    stored = mm.normalize_model({"primary_objective": "Increase FTP"})

    from_inference = mm.merge_model(
        stored, {"primary_objective": ""}, source=mm.SOURCE_INFERRED
    )
    assert from_inference["primary_objective"] == "Increase FTP"

    from_athlete = mm.merge_model(
        stored, {"primary_objective": ""}, source=mm.SOURCE_USER_SET
    )
    assert from_athlete["primary_objective"] == ""


def test_inferred_write_with_nothing_to_say_changes_nothing():
    stored = mm.normalize_model(
        {
            "primary_objective": "Ride more technical trails",
            "secondary_objectives": [{"text": "climb faster"}],
        }
    )

    merged = mm.merge_model(stored, {}, source=mm.SOURCE_INFERRED)

    assert merged["primary_objective"] == stored["primary_objective"]
    assert merged["secondary_objectives"] == stored["secondary_objectives"]


# ---------------------------------------------------------------------------
# Evidence: accrual, promotion, contradiction, bounded weight movement (#563)
# ---------------------------------------------------------------------------


def test_accrual_caps_a_first_sighting():
    """An objective enters as a candidate, not as knowledge."""
    merged = mm.merge_model(
        mm.default_model(),
        {"secondary_objectives": [{"text": "ride more trails", "confidence": 0.9}]},
        accrue=True,
    )

    assert merged["secondary_objectives"][0]["confidence"] == pytest.approx(
        mm.INITIAL_CONFIDENCE_CAP
    )
    assert merged["secondary_objectives"][0]["observation_count"] == 1


def test_accrual_strengthens_a_repeated_entry():
    stored = mm.merge_model(
        mm.default_model(),
        {"secondary_objectives": [{"text": "ride more trails"}]},
        accrue=True,
    )

    merged = mm.merge_model(
        stored, {"secondary_objectives": [{"text": "ride more trails"}]}, accrue=True
    )

    entry = merged["secondary_objectives"][0]
    assert entry["confidence"] == pytest.approx(
        mm.INITIAL_CONFIDENCE_CAP + mm.CONFIDENCE_STEP
    )
    assert entry["observation_count"] == 2


def test_without_accrual_an_entry_is_replaced_not_strengthened():
    """A direct write states the model; only evidence accrues."""
    stored = mm.normalize_model(
        {"secondary_objectives": [{"text": "ride more trails", "confidence": 0.8}]}
    )

    merged = mm.merge_model(
        stored,
        {"secondary_objectives": [{"text": "ride more trails", "confidence": 0.2}]},
        accrue=False,
    )

    assert merged["secondary_objectives"][0]["confidence"] == pytest.approx(0.2)


def test_promotion_needs_both_confidence_and_recurrence():
    """One well-scored sighting is still one sighting."""
    once = mm.normalize_model(
        {
            "secondary_objectives": [
                {"text": "ride trails", "confidence": 0.9, "observation_count": 1}
            ]
        }
    )
    assert mm.promote_primary_objective(once)["primary_objective"] == ""

    twice = mm.normalize_model(
        {
            "secondary_objectives": [
                {"text": "ride trails", "confidence": 0.9, "observation_count": 2}
            ]
        }
    )
    assert mm.promote_primary_objective(twice)["primary_objective"] == "ride trails"


def test_promotion_never_replaces_a_hand_set_objective():
    stored = mm.normalize_model(
        {
            "primary_objective": "Ride the Trans-Alp",
            "primary_objective_source": "user_set",
            "secondary_objectives": [
                {"text": "win races", "confidence": 0.99, "observation_count": 9}
            ],
        }
    )

    assert (
        mm.promote_primary_objective(stored)["primary_objective"] == "Ride the Trans-Alp"
    )


def test_a_promoted_objective_leaves_the_secondary_list():
    stored = mm.normalize_model(
        {
            "secondary_objectives": [
                {"text": "ride trails", "confidence": 0.9, "observation_count": 3},
                {"text": "climb faster", "confidence": 0.4},
            ]
        }
    )

    promoted = mm.promote_primary_objective(stored)

    assert promoted["primary_objective"] == "ride trails"
    assert [e["text"] for e in promoted["secondary_objectives"]] == ["climb faster"]


def test_a_contradicted_entry_keeps_its_text_and_gains_a_reason():
    """The tension goes to the athlete; the coach does not overrule them."""
    flagged = mm.contradict_entry(
        {"text": "Perform at races", "confidence": 0.7},
        note="No race on your calendar.",
    )

    assert flagged["text"] == "Perform at races"
    assert flagged["status"] == mm.STATUS_CONTRADICTED
    assert flagged["contradiction_note"] == "No race on your calendar."
    assert flagged["confidence"] < 0.7


def test_weight_evidence_is_bounded_per_batch():
    moved = mm.apply_weight_evidence(mm.DEFAULT_WEIGHTS, {"enjoyment": 0.9})

    assert (
        moved["enjoyment"] - mm.DEFAULT_WEIGHTS["enjoyment"]
        <= mm.MAX_WEIGHT_NUDGE + 1e-9
    )
    assert sum(moved.values()) == pytest.approx(1.0)


def test_weight_evidence_ignores_a_pinned_component():
    moved = mm.apply_weight_evidence(
        {"enjoyment": 0.4}, {"enjoyment": 0.05}, pinned=["enjoyment"]
    )

    assert moved["enjoyment"] == pytest.approx(0.4)


def test_weight_evidence_ignores_unknown_components():
    moved = mm.apply_weight_evidence(mm.DEFAULT_WEIGHTS, {"vibes": 0.5})

    assert moved == mm.DEFAULT_WEIGHTS


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db() -> AsyncSession:
    """Yield a fresh session that is rolled back after each test."""
    async with TestSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@pytest_asyncio.fixture
async def user(db: AsyncSession) -> models.User:
    return await crud.create_user(
        db, email="motivation@example.com", name="Rider", hashed_password="x"
    )


async def test_round_trip_through_the_database(db: AsyncSession, user):
    await crud.upsert_athlete_motivation_model(
        db,
        user.id,
        updates={
            "primary_objective": "Maximize enjoyable technical trail riding",
            "secondary_objectives": ["Improve climbing speed"],
            "constraints": ["Avoid unnecessary crash risk"],
            "utility_weights": {"enjoyment": 0.6},
        },
        source=mm.SOURCE_USER_SET,
    )

    stored = crud.motivation_model_as_dict(
        await crud.get_athlete_motivation_model(db, user.id)
    )

    assert stored["primary_objective"] == "Maximize enjoyable technical trail riding"
    assert stored["primary_objective_source"] == mm.SOURCE_USER_SET
    assert [e["text"] for e in stored["secondary_objectives"]] == ["Improve climbing speed"]
    assert [e["text"] for e in stored["constraints"]] == ["Avoid unnecessary crash risk"]
    assert sum(stored["utility_weights"].values()) == pytest.approx(1.0)


async def test_an_athlete_with_no_row_reads_as_the_default_model(db: AsyncSession, user):
    stored = crud.motivation_model_as_dict(
        await crud.get_athlete_motivation_model(db, user.id)
    )

    assert stored == mm.default_model()


async def test_persisted_user_set_objective_survives_an_inference_pass(db: AsyncSession, user):
    """The database half of the clobber guard (#342/#345/#346)."""
    await crud.upsert_athlete_motivation_model(
        db,
        user.id,
        updates={"primary_objective": "Ride every trail in the Black Forest"},
        source=mm.SOURCE_USER_SET,
    )

    await crud.upsert_athlete_motivation_model(
        db,
        user.id,
        updates={"primary_objective": "Increase FTP by 20 W"},
        source=mm.SOURCE_INFERRED,
    )

    stored = crud.motivation_model_as_dict(
        await crud.get_athlete_motivation_model(db, user.id)
    )
    assert stored["primary_objective"] == "Ride every trail in the Black Forest"


async def test_a_stored_row_always_reads_back_normalized(db: AsyncSession, user):
    """A row written before an invariant existed is repaired on read."""
    db.add(
        models.AthleteMotivationModel(
            user_id=user.id,
            primary_objective="Enjoy riding",
            secondary_objectives=[],
            constraints=[],
            utility_weights={"enjoyment": 5.0},
            pinned_weights=[],
            updated_at=NOW,
        )
    )
    await db.flush()

    stored = crud.motivation_model_as_dict(
        await crud.get_athlete_motivation_model(db, user.id)
    )

    assert sum(stored["utility_weights"].values()) == pytest.approx(1.0)
