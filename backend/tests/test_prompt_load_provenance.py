"""What the coach is told about a load it did not measure (#579).

Sessions without a power meter now carry a derived load instead of a silent
zero. That is worth far more than a zero and far less than a measurement — and
a coach that cannot tell the two apart will report rising *cycling* form on the
back of gym work.
"""

from __future__ import annotations

from services.prompts import ESTIMATED_LOAD_RULE, ride_metrics_context_section


class _Metric:
    def __init__(self, **kwargs):
        self.activity_date = kwargs.get("activity_date", "2026-08-06")
        self.sport_type = kwargs.get("sport_type", "Ride")
        self.ride_purpose = kwargs.get("ride_purpose", "endurance")
        self.classification_confidence = kwargs.get("classification_confidence", "high")
        self.classification_reason = None
        self.duration_seconds = kwargs.get("duration_seconds", 3600)
        self.tss = kwargs.get("tss", 80.0)
        self.tss_source = kwargs.get("tss_source", "power")
        self.normalized_power_w = kwargs.get("normalized_power_w", 240)
        self.avg_power_w = None
        self.ctl_after = 55.0
        self.atl_after = 60.0
        self.tsb_after = -5.0
        self.coach_note = None
        self.user_note = None
        self.feel_legs = None


def test_a_measured_load_is_still_written_as_tss():
    section = ride_metrics_context_section([_Metric()])
    assert "TSS 80" in section
    # Nothing was estimated, so the athlete pays no tokens for the legend.
    assert ESTIMATED_LOAD_RULE not in section


def test_an_estimated_load_is_marked_as_one():
    section = ride_metrics_context_section(
        [
            _Metric(
                sport_type="WeightTraining",
                ride_purpose="strength",
                tss=34.0,
                tss_source="heart_rate",
                normalized_power_w=None,
            )
        ]
    )
    assert "load ~34 (estimated from HR)" in section
    # The number must never appear as a bare TSS the coach can compare against a
    # power-based figure.
    assert "TSS 34" not in section


def test_the_legend_appears_exactly_when_an_estimate_does():
    powered_only = ride_metrics_context_section([_Metric()])
    mixed = ride_metrics_context_section(
        [
            _Metric(),
            _Metric(
                activity_date="2026-08-05",
                sport_type="Hike",
                ride_purpose="hike",
                tss=25.0,
                tss_source="duration",
                normalized_power_w=None,
            ),
        ]
    )
    assert ESTIMATED_LOAD_RULE not in powered_only
    assert ESTIMATED_LOAD_RULE in mixed


def test_the_legend_says_the_load_is_real_fatigue_and_not_a_measurement():
    """Both halves matter. Told only that it is an estimate, the coach discounts
    it back to the zero this issue exists to remove; told only that it is load,
    it quotes it like a power meter reading."""
    assert "real fatigue" in ESTIMATED_LOAD_RULE
    assert "estimated, not measured" in ESTIMATED_LOAD_RULE
    assert "cycling form" in ESTIMATED_LOAD_RULE


def test_a_row_from_before_the_column_existed_reads_as_it_always_did():
    """``tss_source`` is NULL on rows written before #579; those loads only ever
    came from power or the provider, so they stay plain TSS."""
    section = ride_metrics_context_section([_Metric(tss_source=None)])
    assert "TSS 80" in section
    assert ESTIMATED_LOAD_RULE not in section
