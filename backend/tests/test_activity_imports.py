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
