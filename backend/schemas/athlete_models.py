"""The motivation, athlete and performance models, and the training ROI."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import ConfigDict, Field, field_validator, model_validator

from domain.roi_recommendation import recommend_training_roi
from schemas.base import CamelModel, _to_camel


MotivationSource = Literal["inferred", "user_set"]


MotivationEntryStatus = Literal["active", "contradicted", "retired"]


MotivationComponent = Literal[
    "enjoyment", "adaptation", "consistency", "health", "race_performance"
]


class MotivationEntrySchema(CamelModel):
    """One secondary objective or constraint, with the evidence behind it (#562).

    The provenance fields are not decoration: the athlete can only correct an
    inferred objective they can see the reason for (#567), and #566 needs to know
    how long an objective has been standing before it moves a weight.
    """

    text: str
    confidence: float = 0.35
    source: MotivationSource = "inferred"
    source_snippet: str = ""
    status: MotivationEntryStatus = "active"
    contradiction_note: str | None = None
    first_observed_at: str | None = None
    last_confirmed_at: str | None = None

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class AthleteMotivationModelSchema(CamelModel):
    """What the athlete is optimizing for (#562).

    A *preference* ("likes MTB") lives in :class:`AthleteMemoryFactSchema`. This
    is the *objective* ("uses fitness to maximize enjoyable technical trail
    riding") — what every planning decision is scored against (#564).
    """

    primary_objective: str = ""
    primary_objective_source: MotivationSource = "inferred"
    primary_objective_confidence: float = 0.0
    primary_objective_snippet: str = ""
    secondary_objectives: list[MotivationEntrySchema] = Field(default_factory=list)
    constraints: list[MotivationEntrySchema] = Field(default_factory=list)
    # Spans exactly MotivationComponent and sums to 1.0 — guaranteed by the
    # persist gate in domain.motivation_model, not re-checked by readers.
    utility_weights: dict[str, float] = Field(default_factory=dict)
    pinned_weights: list[MotivationComponent] = Field(default_factory=list)
    # modality -> affinity in [0, 1]. Independent scores, not a distribution —
    # what the weights cannot express: how the athlete wants it delivered (#564).
    modality_affinity: dict[str, float] = Field(default_factory=dict)
    updated_at: datetime | None = None

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class AthleteMotivationModelRequest(CamelModel):
    """An athlete's own edit of their motivation model (#567).

    Every field is optional so a partial edit is a valid request; whatever is
    sent is stored as ``user_set`` and is protected from later inference passes.
    """

    primary_objective: str | None = None
    secondary_objectives: list[MotivationEntrySchema] | None = None
    constraints: list[MotivationEntrySchema] | None = None
    utility_weights: dict[str, float] | None = None
    pinned_weights: list[MotivationComponent] | None = None
    modality_affinity: dict[str, float] | None = None

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class MotivationWeightEventSchema(CamelModel):
    """One recorded change to the utility weight vector (#566).

    The weights are visible to the athlete and decide what the planner
    recommends, so a change they did not make has to be able to account for
    itself. ``rules`` and ``evidence`` are absent for an athlete's own edit:
    nothing was inferred, so there is nothing to justify.
    """

    id: str
    source: MotivationSource
    weights_before: dict[str, float] = Field(default_factory=dict)
    weights_after: dict[str, float] = Field(default_factory=dict)
    # Only the components that actually moved, signed.
    deltas: dict[str, float] = Field(default_factory=dict)
    rules: list[dict[str, Any]] | None = None
    evidence: dict[str, Any] | None = None
    pinned: list[str] = Field(default_factory=list)
    recorded_at: datetime | None = None

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class AthleteModelSchema(CamelModel):
    """The long-term structured athlete model (#384).

    Captures durable physiological/performance characteristics. Quantitative
    anchors (``ftp_watts``, ``vo2max``) are optional; the remaining qualitative
    fields default to empty so a never-derived model round-trips as blanks.
    """

    ftp_watts: Optional[int] = None
    vo2max: Optional[float] = None
    pacing_quality: str = ""
    recovery_ability: str = ""
    threshold_durability: str = ""
    heat_tolerance: str = ""
    preferred_training_style: str = ""
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    risk_factors: list[str] = Field(default_factory=list)
    summary: str = ""
    confidence: float = 0.0
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class AthleteModelRequest(CamelModel):
    """Athlete-authored edits to the long-term model.

    Excludes ``confidence`` and ``updated_at`` — those are coach/server owned.
    """

    ftp_watts: Optional[int] = None
    vo2max: Optional[float] = None
    pacing_quality: str = ""
    recovery_ability: str = ""
    threshold_durability: str = ""
    heat_tolerance: str = ""
    preferred_training_style: str = ""
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    risk_factors: list[str] = Field(default_factory=list)
    summary: str = ""

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class AthletePerformanceAttributeSchema(CamelModel):
    """One inferred physiological attribute in the Athlete Performance Model (#475).

    Every attribute is emitted with a ``confidence`` and never presented as fact:
    quantitative attributes carry an ``estimate`` (+ ``unit``); qualitative ones
    carry a ``score`` (e.g. ``high``/``above_average``/``unknown``). ``evidence``
    lists the signals behind it and ``missing_information`` what would sharpen it.

    ``estimate_low``/``estimate_high`` bound the estimate when the evidence
    supports a range rather than a point, and ``validation_protocol`` carries the
    concrete test that would settle it — offered instead of asserting a number the
    data does not pin down (#604). All three are absent on attributes that are
    qualitative or simply known.
    """

    estimate: Optional[float] = None
    estimate_low: Optional[float] = None
    estimate_high: Optional[float] = None
    score: Optional[str] = None
    confidence: float
    unit: Optional[str] = None
    evidence: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    validation_protocol: Optional[str] = None

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class AthletePerformanceLimiterSchema(CamelModel):
    """One candidate physiological limiter in the ranked list (#477).

    Surfaced as an inference, never a fact: it carries a ``confidence`` and both
    the ``evidence`` for and the ``counterEvidence`` against it. ``limiter`` is one
    of ``threshold``/``vo2max``/``endurance_durability``/``insufficient_data``.
    """

    limiter: str
    confidence: float
    evidence: list[str] = Field(default_factory=list)
    counter_evidence: list[str] = Field(default_factory=list)

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class TrainingRoiSystemGainSchema(CamelModel):
    """Expected training return for one physiological system (#478).

    ``system`` is one of ``threshold``/``vo2max``/``endurance``/``anaerobic``;
    ``gain`` is a coarse return bucket (``large``/``moderate``/``small``/
    ``maintenance``) with a short ``rationale``.
    """

    system: str
    gain: str
    rationale: str = ""

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class TrainingRoiEmphasisSchema(CamelModel):
    """A suggested weekly emphasis line, e.g. ``2× Threshold`` (#478)."""

    system: str
    label: str
    sessions: int

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class TrainingUtilityOptionSchema(CamelModel):
    """One (system, modality) option, scored by expected athlete utility (#564).

    Both sub-scores are carried, not just the total: a recommendation that cannot
    say how much of itself was physiology and how much was preference is not
    reviewable — by the athlete, by the coach explaining it (#565), or by whoever
    has to debug why it changed.
    """

    system: str
    modality: str
    gain: str = ""
    physiological_score: float = 0.0
    motivation_score: float = 0.0
    utility: float = 0.0
    components: dict[str, float] = Field(default_factory=dict)
    excluded_by: Optional[str] = None

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class TrainingUtilitySchema(CamelModel):
    """The utility ranking behind a recommendation (#564).

    ``weights`` is recorded rather than recomputed on read: a recommendation
    whose weights are not stored cannot be audited when the ranking changes next
    month.
    """

    options: list[TrainingUtilityOptionSchema] = Field(default_factory=list)
    excluded: list[TrainingUtilityOptionSchema] = Field(default_factory=list)
    weights: dict[str, float] = Field(default_factory=dict)
    modality_affinity: dict[str, float] = Field(default_factory=dict)

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class TrainingRoiRecommendationSchema(CamelModel):
    """ROI-based recommendation derived from the model + limiter (#478).

    Machine-readable expected gain per system plus a suggested weekly emphasis and
    a natural-language rationale that cites the model. ``sufficient`` is ``False``
    when the model has no confident limiter — the caller should keep its own
    periodization rather than act on this.
    """

    sufficient: bool = False
    limiter: Optional[str] = None
    confidence: float = 0.0
    hypothesis: str = ""
    rationale: str = ""
    expected_gain: list[TrainingRoiSystemGainSchema] = Field(default_factory=list)
    weekly_emphasis: list[TrainingRoiEmphasisSchema] = Field(default_factory=list)
    # Present only when a motivation model was supplied (#564); absent means the
    # recommendation is physiology-only, exactly as it was before.
    utility: Optional[TrainingUtilitySchema] = None

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class AthletePerformanceModelSchema(CamelModel):
    """Deterministic, per-attribute Athlete Performance Model (#475).

    Complements :class:`AthleteModelSchema` (LLM qualitative profile) with a
    rule-based, evidence-backed quantitative model. ``attributes`` is keyed by
    attribute name (``vo2max``, ``ftp``, ``map``, ``fractional_utilization``,
    ``aerobic_endurance``, ``fatigue_resistance``, ``anaerobic_capacity``, …).
    ``likelyLimiter`` and the ranked ``limiters`` list come from limiter
    detection (#477); ``recommendations`` is the ROI mapping derived from them
    (#478).
    """

    attributes: dict[str, AthletePerformanceAttributeSchema] = Field(
        default_factory=dict
    )
    likely_limiter: Optional[str] = None
    limiters: list[AthletePerformanceLimiterSchema] = Field(default_factory=list)
    recommendations: Optional[TrainingRoiRecommendationSchema] = None
    source_window_days: Optional[int] = None
    derived_from_rides: int = 0
    updated_at: Optional[datetime] = None

    @field_validator("limiters", mode="before")
    @classmethod
    def _limiters_default(cls, v: object) -> object:
        # The DB column is nullable (unset before limiter detection ran, or on
        # rows predating the migration); present it as an empty list.
        return v if v is not None else []

    @model_validator(mode="after")
    def _derive_recommendations(self) -> "AthletePerformanceModelSchema":
        # The ROI recommendation (#478) is a pure derivation of attributes + the
        # ranked limiter list, so compute it here rather than persist it.
        if self.recommendations is None:
            attrs = {k: v.model_dump() for k, v in self.attributes.items()}
            limiters = [lim.model_dump() for lim in self.limiters]
            self.recommendations = TrainingRoiRecommendationSchema.model_validate(
                recommend_training_roi(attrs, limiters)
            )
        return self

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        from_attributes=True,
    )
