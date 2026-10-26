"""Metric snapshots and the readiness score."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, computed_field

from domain.fitness_ledger import ledger_from_row
from schemas.base import CamelModel


# ---------------------------------------------------------------------------
# Athlete metric history
# ---------------------------------------------------------------------------


class AthleteMetricSnapshotSchema(CamelModel):
    recorded_at: str
    ftp: Optional[int] = None
    map_5min: Optional[int] = None
    """Best 5-minute power (W) at the time of the snapshot — the maximal
    aerobic power proxy.  ``null`` on snapshots recorded before this was
    tracked, or when no ride in the window contained a 5-minute effort."""
    ctl: Optional[float] = None
    atl: Optional[float] = None
    tsb: Optional[float] = None
    ctl_by_sport: Optional[dict[str, float]] = None
    """Per-sport chronic load at the time of the snapshot (#713). ``ctl`` above
    is a single sport's rung out of this; ``atl`` is the aggregate across all of
    them. ``null`` on snapshots recorded before the split, which mean cycling."""
    source: str = "strava_analysis"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def form_ctl(self) -> Optional[float]:
        """The main sport's CTL: the fitness ``tsb`` is the form of.

        ``ctl`` is the rung of the sport logged last, so a rider's chart dropped
        to about 1 on every gym day (2026-10-15). Derived here, not in the
        browser, so the rule that picks the main sport exists once
        (``LoadLedger.form_sport``).
        """
        if self.ctl is None:
            return None
        ledger = ledger_from_row(
            self.ctl_by_sport, ctl_after=self.ctl, atl_after=self.atl
        )
        return round(ledger.ctl(ledger.form_sport()), 1)


class MetricsHistoryResponse(BaseModel):
    snapshots: list[AthleteMetricSnapshotSchema]


class ReasoningItem(BaseModel):
    """A single supporting-evidence bullet, tagged with its knowledge source.

    The ``source`` distinguishes where the knowledge comes from (issue #377) so
    the athlete can tell a personal observation about themselves apart from
    established sports science and from the coach's read of their metrics.
    """

    source: str
    """One of ``personal_observation``, ``scientific_evidence``, ``coach_inference``."""
    text: str
    """The reasoning bullet itself, without any source-label prefix."""


class ReadinessRecommendation(BaseModel):
    """A single readiness recommendation together with the evidence behind it.

    Each recommendation is transparent about *why* it was made: ``reasoning``
    holds short supporting-evidence bullets, each tagged with its knowledge
    source (personal observation, scientific evidence, or coach inference).
    """

    recommendation: str
    """The actionable advice, e.g. "Prioritise 2–3 easy recovery rides this week."."""
    reasoning: list[ReasoningItem] = []
    """Source-tagged supporting-evidence bullets explaining the recommendation."""


class ReadinessScoreResponse(BaseModel):
    """Response for the GET /ai/readiness-score endpoint."""

    score: float
    """Combined readiness score (0–100). Blends form (TSB) and fitness (CTL)."""
    form_score: float
    """Form component of the score (0–100). Peaks at TSB +5 to +15."""
    fitness_score: float
    """Fitness component of the score (0–100). Based on CTL (42-day load)."""
    ctl: float
    """Chronic Training Load — 42-day exponential weighted average of daily TSS."""
    atl: float
    """Acute Training Load — 7-day exponential weighted average of daily TSS."""
    tsb: float
    """Training Stress Balance — CTL minus ATL (form/freshness indicator)."""
    days_until_race: int
    """Calendar days remaining until race_date (0 when race day or past)."""
    race_date: Optional[str] = None
    """ISO date string of the upcoming race, or None when not set."""
    projected_score: Optional[float] = None
    """Readiness score projected at race day using the current plan."""
    projected_ctl: Optional[float] = None
    """Projected CTL at race day."""
    projected_atl: Optional[float] = None
    """Projected ATL at race day."""
    projected_tsb: Optional[float] = None
    """Projected TSB at race day."""
    recommendations: list[ReadinessRecommendation] = []
    """Actionable tips to improve race readiness, each with its supporting evidence."""
