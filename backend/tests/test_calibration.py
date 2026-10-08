"""Calibration of the coach's predictions (ai-trainer-ops#27).

Two halves. The arithmetic — bins, Brier, expected calibration error — checked
against values worked out by hand. And the reason the measurement needed a new
column at all: the stored ``confidence`` moves with the outcome, so the test
that matters most here is the one showing that binning it would report a
perfectly calibrated coach as perfectly separating, and that
``stated_confidence`` does not.
"""

from __future__ import annotations

import pytest

import crud
from auth import hash_password
from scripts import calibration_report
from services import calibration
from tests.conftest import TestSessionLocal


async def _create_user(email: str) -> str:
    async with TestSessionLocal() as db:
        user = await crud.create_user(
            db, email=email, name="T", hashed_password=hash_password("pw")
        )
        await db.commit()
        return user.id


async def _predict(user_id: str, text: str, confidence: float) -> str:
    async with TestSessionLocal() as db:
        row = await crud.record_athlete_prediction(
            db,
            user_id,
            prediction=text,
            expected_outcome="it happens",
            confidence=confidence,
        )
        await db.commit()
        return row.id


async def _evaluate(user_id: str, prediction_id: str, correct: bool) -> None:
    async with TestSessionLocal() as db:
        await crud.evaluate_athlete_prediction(
            db, user_id, prediction_id, correct=correct, actual_outcome="observed"
        )
        await db.commit()


async def _stored(prediction_id: str, user_id: str):
    async with TestSessionLocal() as db:
        return await crud.get_athlete_prediction(db, user_id, prediction_id)


# ---------------------------------------------------------------------------
# The arithmetic
# ---------------------------------------------------------------------------


def test_nothing_evaluated_reports_nothing():
    result = calibration.reliability([])
    assert result.count == 0
    assert result.bins == ()
    assert result.brier is None
    assert result.expected_calibration_error is None


def test_a_perfectly_calibrated_bucket_has_no_calibration_error():
    # Ten predictions at 0.6, six came true.
    pairs = [(0.6, True)] * 6 + [(0.6, False)] * 4
    result = calibration.reliability(pairs)
    assert len(result.bins) == 1
    assert result.bins[0].hit_rate == pytest.approx(0.6)
    assert result.expected_calibration_error == pytest.approx(0.0)
    # 6 × 0.4² + 4 × 0.6² over 10
    assert result.brier == pytest.approx((6 * 0.16 + 4 * 0.36) / 10)


def test_an_overconfident_coach_shows_the_gap():
    # Says 0.9, is right half the time.
    pairs = [(0.9, True), (0.9, False)] * 5
    result = calibration.reliability(pairs)
    assert result.bins[0].mean_confidence == pytest.approx(0.9)
    assert result.bins[0].hit_rate == pytest.approx(0.5)
    assert result.expected_calibration_error == pytest.approx(0.4)


def test_bins_are_half_open_and_the_last_one_holds_certainty():
    result = calibration.reliability([(0.1, True), (0.0999, False), (1.0, True)])
    bounds = [(b.lower, b.upper, b.count) for b in result.bins]
    assert bounds == [(0.0, 0.1, 1), (0.1, 0.2, 1), (0.9, 1.0, 1)]


def test_the_error_weights_each_bucket_by_its_size():
    # 0.2-bucket: 4 predictions, hit rate 0.25, mean 0.2 -> gap 0.05
    # 0.8-bucket: 1 prediction, hit rate 0.0, mean 0.8 -> gap 0.8
    pairs = [(0.2, True), (0.2, False), (0.2, False), (0.2, False), (0.8, False)]
    result = calibration.reliability(pairs)
    assert result.expected_calibration_error == pytest.approx(
        4 / 5 * 0.05 + 1 / 5 * 0.8
    )


def test_confidence_outside_the_unit_interval_is_clamped_not_dropped():
    result = calibration.reliability([(1.4, True), (-0.2, False)])
    assert result.count == 2
    assert [b.lower for b in result.bins] == [0.0, 0.9]


@pytest.mark.parametrize("width", [0, -0.1, 1.5])
def test_a_bin_width_outside_the_unit_interval_is_refused(width):
    with pytest.raises(ValueError):
        calibration.reliability([(0.5, True)], width=width)


def test_the_report_renders_every_non_empty_bucket():
    text = calibration.render_markdown(
        "Measured", calibration.reliability([(0.6, True), (0.6, False), (0.9, True)])
    )
    assert "3 evaluated predictions, 2 came true." in text
    assert "| 0.6–0.7 | 2 | 0.60 | 0.50 |" in text
    assert "| 0.9–1.0 | 1 | 0.90 | 1.00 |" in text


def test_an_empty_report_says_so():
    assert "_No evaluated predictions._" in calibration.render_markdown(
        "Measured", calibration.reliability([])
    )


# ---------------------------------------------------------------------------
# What is stated stays stated
# ---------------------------------------------------------------------------


async def test_a_new_prediction_records_the_confidence_it_was_made_with():
    user_id = await _create_user("calibration-new@example.com")
    prediction_id = await _predict(user_id, "FTP holds this week", 0.6)

    row = await _stored(prediction_id, user_id)
    assert row.stated_confidence == pytest.approx(0.6)


async def test_evaluation_moves_confidence_but_not_what_was_stated():
    user_id = await _create_user("calibration-eval@example.com")
    prediction_id = await _predict(user_id, "FTP holds this week", 0.6)

    await _evaluate(user_id, prediction_id, correct=True)

    row = await _stored(prediction_id, user_id)
    assert row.confidence == pytest.approx(0.8)
    assert row.stated_confidence == pytest.approx(0.6)


async def test_refreshing_a_pending_duplicate_keeps_the_original_statement():
    user_id = await _create_user("calibration-refresh@example.com")
    prediction_id = await _predict(user_id, "FTP holds this week", 0.6)
    await _predict(user_id, "FTP holds this week", 0.9)

    row = await _stored(prediction_id, user_id)
    assert row.stated_confidence == pytest.approx(0.6)


async def test_an_athletes_edit_does_not_rewrite_what_the_coach_said():
    user_id = await _create_user("calibration-edit@example.com")
    prediction_id = await _predict(user_id, "FTP holds this week", 0.6)
    async with TestSessionLocal() as db:
        await crud.update_athlete_prediction(db, user_id, prediction_id, confidence=0.1)
        await db.commit()

    row = await _stored(prediction_id, user_id)
    assert row.confidence == pytest.approx(0.1)
    assert row.stated_confidence == pytest.approx(0.6)


# ---------------------------------------------------------------------------
# Why the column exists
# ---------------------------------------------------------------------------


async def test_the_stored_confidence_would_fake_a_separation_the_coach_never_made():
    """Two calls at 0.5, one right, one wrong: a perfectly calibrated coach.

    After evaluation the stored values are 0.7 and 0.3, so binning them would
    report a coach whose high-confidence calls always land and whose low ones
    never do. The stated values put both in one bucket at 50%, which is the
    truth.
    """
    user_id = await _create_user("calibration-bias@example.com")
    hit = await _predict(user_id, "Sunday is the long ride", 0.5)
    miss = await _predict(user_id, "Tuesday feels fresh", 0.5)
    await _evaluate(user_id, hit, correct=True)
    await _evaluate(user_id, miss, correct=False)

    async with TestSessionLocal() as db:
        measured = calibration.reliability(await calibration.measured_pairs(db, user_id))
    stored = calibration.reliability(
        [
            ((await _stored(hit, user_id)).confidence, True),
            ((await _stored(miss, user_id)).confidence, False),
        ]
    )

    assert [(b.lower, b.hit_rate) for b in measured.bins] == [(0.5, 0.5)]
    assert measured.expected_calibration_error == pytest.approx(0.0)
    assert [(b.lower, b.hit_rate) for b in stored.bins] == [(0.3, 0.0), (0.7, 1.0)]


async def test_pending_predictions_are_not_measured():
    user_id = await _create_user("calibration-pending@example.com")
    await _predict(user_id, "FTP holds this week", 0.6)

    async with TestSessionLocal() as db:
        assert await calibration.measured_pairs(db, user_id) == []


async def test_measurement_can_be_limited_to_one_athlete():
    mine = await _create_user("calibration-mine@example.com")
    theirs = await _create_user("calibration-theirs@example.com")
    for user_id in (mine, theirs):
        await _evaluate(user_id, await _predict(user_id, "call", 0.7), correct=True)

    async with TestSessionLocal() as db:
        assert len(await calibration.measured_pairs(db, mine)) == 1
        assert len(await calibration.measured_pairs(db)) == 2


# ---------------------------------------------------------------------------
# Older rows: an estimate, kept apart
# ---------------------------------------------------------------------------


async def _legacy(user_id: str, text: str, stored: float, status: str) -> None:
    """A row as it looked before ``stated_confidence`` existed."""
    prediction_id = await _predict(user_id, text, 0.5)
    async with TestSessionLocal() as db:
        row = await crud.get_athlete_prediction(db, user_id, prediction_id)
        row.stated_confidence = None
        row.confidence = stored
        row.status = status
        await db.commit()


async def test_older_rows_are_estimated_by_undoing_the_nudge():
    user_id = await _create_user("calibration-legacy@example.com")
    await _legacy(user_id, "a", 0.7, "correct")
    await _legacy(user_id, "b", 0.4, "incorrect")

    async with TestSessionLocal() as db:
        pairs, skipped = await calibration.reconstructed_pairs(db, user_id)
        assert await calibration.measured_pairs(db, user_id) == []

    assert sorted(pairs) == [(0.5, True), (0.6, False)]
    assert skipped == 0


async def test_a_possibly_clamped_row_is_skipped_rather_than_guessed():
    user_id = await _create_user("calibration-clamped@example.com")
    await _legacy(user_id, "a", 1.0, "correct")
    await _legacy(user_id, "b", 0.0, "incorrect")

    async with TestSessionLocal() as db:
        pairs, skipped = await calibration.reconstructed_pairs(db, user_id)

    assert pairs == []
    assert skipped == 2


async def test_a_row_with_a_stated_value_is_never_reconstructed():
    user_id = await _create_user("calibration-both@example.com")
    await _evaluate(user_id, await _predict(user_id, "call", 0.7), correct=True)

    async with TestSessionLocal() as db:
        pairs, skipped = await calibration.reconstructed_pairs(db, user_id)

    assert (pairs, skipped) == ([], 0)


# ---------------------------------------------------------------------------
# The script
# ---------------------------------------------------------------------------


async def test_the_report_keeps_measured_and_reconstructed_apart(monkeypatch):
    user_id = await _create_user("calibration-script@example.com")
    await _evaluate(user_id, await _predict(user_id, "now", 0.6), correct=True)
    await _legacy(user_id, "then", 0.7, "correct")
    monkeypatch.setattr(calibration_report, "async_session_maker", TestSessionLocal)

    text = await calibration_report.build(reconstructed=True)

    measured, reconstructed = text.split("## Reconstructed")
    assert "1 evaluated predictions, 1 came true." in measured
    assert "| 0.6–0.7 | 1 | 0.60 | 1.00 |" in measured
    assert "| 0.5–0.6 | 1 | 0.50 | 1.00 |" in reconstructed
    assert "0 rows skipped" in reconstructed


async def test_the_report_without_reconstruction_has_one_section(monkeypatch):
    monkeypatch.setattr(calibration_report, "async_session_maker", TestSessionLocal)
    text = await calibration_report.build(reconstructed=False)
    assert "## Measured" in text
    assert "Reconstructed" not in text


def test_the_script_parses_its_flag(monkeypatch, capsys):
    async def fake_build(reconstructed: bool) -> str:
        return f"built reconstructed={reconstructed}"

    monkeypatch.setattr(calibration_report, "build", fake_build)
    assert calibration_report.main(["--reconstructed"]) == 0
    assert "built reconstructed=True" in capsys.readouterr().out
