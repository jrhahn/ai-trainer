from services.activity_imports import ImportedActivity, fallback_fingerprint
from services.analysis import build_ride_metrics_chain
from services.intervals_service import (
    intervals_activity_id,
    map_activity_to_imported_activity,
)


def test_imported_activity_normalizes_strava_to_legacy_metric_input():
    activity = ImportedActivity(
        source="strava",
        external_activity_id="12345",
        name="Morning Ride",
        start_datetime="2026-06-11T07:00:00Z",
        activity_date="2026-06-11",
        sport_type="Ride",
        duration_seconds=3600,
        streams={"watts": {"data": [200, 210]}, "time": {"data": [0, 1]}},
    )

    ride = activity.to_ride_input()

    assert ride["strava_activity_id"] == 12345
    assert ride["activity_source"] == "strava"
    assert ride["external_activity_id"] == "12345"
    assert ride["activity_name"] == "Morning Ride"


def test_intervals_mapper_uses_same_normalized_contract_with_stable_legacy_id():
    imported = map_activity_to_imported_activity(
        {
            "id": "i123",
            "name": "Intervals Ride",
            "type": "Ride",
            "start_date_local": "2026-06-10T09:00:00",
            "moving_time": 1800,
            "average_watts": 180,
            "icu_weighted_avg_watts": 190,
            "icu_training_load": 45.0,
        },
        None,
        {},
    )

    assert imported is not None
    ride = imported.to_ride_input()
    assert ride["strava_activity_id"] == intervals_activity_id("i123")
    assert ride["activity_source"] == "intervals"
    assert ride["external_activity_id"] == "i123"
    assert ride["_summary_avg_power_w"] == 180
    assert ride["_summary_np_w"] == 190
    assert ride["_summary_tss"] == 45.0


def test_fallback_fingerprint_is_source_scoped_for_manual_imports():
    first = fallback_fingerprint(
        source="fit",
        name="Manual Ride",
        start_datetime="2026-06-10T09:00:00+00:00",
        activity_date="2026-06-10",
        sport_type="cycling",
        duration_seconds=3600,
    )
    second = fallback_fingerprint(
        source="intervals",
        name="Manual Ride",
        start_datetime="2026-06-10T09:00:00+00:00",
        activity_date="2026-06-10",
        sport_type="cycling",
        duration_seconds=3600,
    )

    assert first != second


def test_metrics_chain_preserves_normalized_source_fields():
    ride = ImportedActivity(
        source="fit",
        external_activity_id="fit-fingerprint",
        name="Uploaded FIT",
        start_datetime="2026-06-10T09:00:00+00:00",
        activity_date="2026-06-10",
        sport_type="cycling",
        duration_seconds=3600,
        streams={"watts": {"data": [200, 210, 220]}, "time": {"data": [0, 1, 2]}},
        metadata={"file_id_serial_number": "abc"},
        legacy_activity_id=98765,
    ).to_ride_input()

    metrics = build_ride_metrics_chain([ride], ftp=250)

    assert metrics[0]["strava_activity_id"] == 98765
    assert metrics[0]["activity_source"] == "fit"
    assert metrics[0]["external_activity_id"] == "fit-fingerprint"
    assert metrics[0]["source_metadata"] == {"file_id_serial_number": "abc"}


# --- Provider power figures over lossy stream recompute (#466) ---


def test_intervals_average_never_borrows_normalized_power():
    """Average power must not fall back to ``icu_weighted_avg_watts`` (NP).

    Conflating them made avg == NP for intervals rides (#466).
    """
    imported = map_activity_to_imported_activity(
        {
            "id": "i77",
            "type": "Ride",
            "start_date_local": "2026-07-01T09:00:00",
            "moving_time": 3600,
            "icu_weighted_avg_watts": 210,  # NP only, no average field
        },
        None,
        {},
    )
    assert imported is not None
    ride = imported.to_ride_input()
    assert ride["_summary_np_w"] == 210
    assert ride["_summary_avg_power_w"] is None


def test_intervals_average_prefers_icu_average_watts():
    imported = map_activity_to_imported_activity(
        {
            "id": "i78",
            "type": "Ride",
            "start_date_local": "2026-07-01T09:00:00",
            "moving_time": 3600,
            "icu_average_watts": 198,
            "average_watts": 201,
            "icu_weighted_avg_watts": 215,
        },
        None,
        {},
    )
    assert imported is not None
    ride = imported.to_ride_input()
    assert ride["_summary_avg_power_w"] == 198
    assert ride["_summary_np_w"] == 215


def test_metrics_chain_prefers_provider_power_over_lossy_stream():
    """A degenerate stream (avg == NP) must not override the provider figures."""
    ride = ImportedActivity(
        source="intervals",
        external_activity_id="i999",
        name="Long aerobic ride",
        start_datetime="2026-07-25T08:00:00",
        activity_date="2026-07-25",
        sport_type="cycling",
        duration_seconds=13622,
        # Flat stream would recompute avg == NP == 100 — the #466 failure mode.
        streams={"watts": {"data": [100, 100, 100]}, "time": {"data": [0, 30, 60]}},
        summary_avg_power_w=201,
        summary_normalized_power_w=215,
        summary_tss=170.0,
    ).to_ride_input()

    metrics = build_ride_metrics_chain([ride], ftp=320)
    m = metrics[0]

    assert m["avg_power_w"] == 201
    assert m["normalized_power_w"] == 215
    assert m["normalized_power_w"] != m["avg_power_w"]  # no longer degenerate
    assert m["intensity_factor"] == round(215 / 320, 3)
    assert m["tss"] == 170.0


def test_metrics_chain_falls_back_to_stream_without_provider_power():
    ride = ImportedActivity(
        source="intervals",
        external_activity_id="i998",
        name="No-summary ride",
        start_datetime="2026-07-25T08:00:00",
        activity_date="2026-07-25",
        sport_type="cycling",
        duration_seconds=3600,
        streams={"watts": {"data": [200, 200, 200, 200]}, "time": {"data": [0, 30, 60, 90]}},
    ).to_ride_input()

    metrics = build_ride_metrics_chain([ride], ftp=250)

    assert metrics[0]["avg_power_w"] == 200  # from the stream, provider absent


# --- ImportedActivity id/key helpers (issue #335) ---

from services.activity_imports import (  # noqa: E402
    ImportedActivity,
    synthetic_activity_id,
)


def test_synthetic_activity_id_is_positive_and_stable():
    a = synthetic_activity_id("intervals", "abc")
    assert a > 0
    assert a == synthetic_activity_id("intervals", "abc")
    assert a != synthetic_activity_id("fit", "abc")


def _imported(**kw) -> ImportedActivity:
    base = dict(
        source="intervals",
        external_activity_id="ext-1",
        name="Ride",
        start_datetime="2026-05-05T08:00:00",
        activity_date="2026-05-05",
    )
    base.update(kw)
    return ImportedActivity(**base)


def test_source_key_uses_external_id_or_fingerprint():
    assert _imported(external_activity_id="ext-1").source_key == "ext-1"
    # without an external id, a deterministic fingerprint is used
    fp = _imported(external_activity_id=None).source_key
    assert isinstance(fp, str) and len(fp) == 64


def test_legacy_metric_id_paths():
    # explicit legacy id wins
    assert _imported(legacy_activity_id=42).legacy_metric_id == 42
    # strava numeric external id is used directly
    assert _imported(source="strava", external_activity_id="999", legacy_activity_id=None).legacy_metric_id == 999
    # strava non-numeric external id falls back to a synthetic id
    val = _imported(source="strava", external_activity_id="not-a-number", legacy_activity_id=None).legacy_metric_id
    assert val > 0
