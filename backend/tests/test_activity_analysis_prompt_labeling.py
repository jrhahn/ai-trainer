"""Guardrail: the activity-analysis prompt must present power as ONE labeled block.

The login/activity-analysis prompt used to hand the model three power numbers
for the same ride with nothing marking which was canonical — the raw activity
dump's ``average_watts`` (avg) and ``weighted_average_watts`` (NP), plus our
stream-mean ``avg_power_w`` — so the coach narrated the wrong one ("averaging
221W", our lossy stream-mean, against the provider's real 201 W avg / 215 W NP).
This suite locks in the #467 fix: a single labeled, unit-tagged power block, no
bare/duplicate power fields in the raw dump, and an instruction to cite only the
labeled figures. Sibling of ``test_prompt_date_anchoring.py``.
"""

from __future__ import annotations

import json

from services import prompts

TZ = "Europe/Berlin"

# Provider figures: avg 201 W, NP 215 W, peak 640 W — the real Darmstadt ride
# from #467, where the summary wrongly said "averaging 221W" (a stream-mean).
ACTIVITY = {
    "id": 999,
    "name": "Darmstadt loop",
    "type": "Ride",
    "sport_type": "cycling",
    "start_date": "2026-07-25T08:00:00Z",
    "start_date_local": "2026-07-25T10:00:00",
    "moving_time": 13622,
    "average_watts": 201,
    "weighted_average_watts": 215,
    "max_watts": 640,
    "average_heartrate": 145,
}


def _msg(user_ftp: int | None = 320) -> str:
    return prompts.analyse_activities_user(
        [ACTIVITY],
        "",
        "",
        sport_type="cycling",
        timezone_name=TZ,
        user_ftp=user_ftp,
    )


def test_power_metrics_block_is_labeled_and_unit_tagged():
    msg = _msg()
    assert "Per-activity power & load (authoritative" in msg
    assert "average power 201 W" in msg
    assert "normalized power 215 W" in msg
    assert "peak power 640 W" in msg


def test_intensity_factor_derived_from_user_ftp():
    msg = _msg(user_ftp=320)
    # IF = NP / FTP = 215 / 320, rounded to 3 dp.
    assert f"intensity factor {round(215 / 320, 3)}" in msg


def test_intensity_factor_omitted_without_ftp():
    msg = _msg(user_ftp=None)
    assert "normalized power 215 W" in msg
    assert "intensity factor" not in msg


def test_raw_dump_drops_bare_power_fields():
    """The unlabeled provider power fields must not survive into the JSON dump."""
    msg = _msg()
    # The raw activity JSON block sits before the labeled power block.
    dump = msg.split("Per-activity power & load")[0]
    assert '"average_watts"' not in dump
    assert '"weighted_average_watts"' not in dump
    assert '"max_watts"' not in dump
    # Non-power context is still handed over.
    assert '"average_heartrate": 145' in dump
    assert '"name": "Darmstadt loop"' in dump


def test_prompt_instructs_labeled_only_citation():
    msg = _msg()
    lowered = msg.lower()
    assert "cite only the labeled" in lowered
    assert "never recompute" in lowered or "never a bare number" in lowered


def test_block_absent_when_no_power_data():
    msg = prompts.analyse_activities_user(
        [{"id": 1, "name": "Yoga", "type": "Yoga"}],
        "",
        "",
        sport_type="cycling",
        timezone_name=TZ,
        user_ftp=320,
    )
    # The block header (not the instruction that names it) must be absent.
    assert "Per-activity power & load (authoritative" not in msg


def test_input_activities_are_not_mutated():
    original = json.dumps(ACTIVITY, sort_keys=True)
    _msg()
    assert json.dumps(ACTIVITY, sort_keys=True) == original


def test_block_helper_uses_type_when_name_missing():
    block = prompts.activity_power_metrics_block(
        [{"type": "VirtualRide", "average_watts": 180}], user_ftp=250
    )
    assert "VirtualRide" in block
    assert "average power 180 W" in block
