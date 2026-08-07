"""What the athlete is actually optimizing for — the motivation model (#562).

Physiology is not the goal. It is the mechanism. A trail rider trains to ride
more technical descents, a bikepacker to enjoy multi-day adventures, a racer to
win; the same threshold session serves all three for different reasons, and only
the last of them is well served by a coach that maximizes adaptation. This module
owns the athlete's objective as data so the planner can optimize for it (#564)
and the coach can explain itself in the athlete's own terms (#565).

The distinction that makes this a separate concept from everything already
stored: **a preference is not an objective**. ``AthleteMemoryFact`` records that
the athlete likes MTB. This records that they use fitness to maximize enjoyable
technical trail riding. The first is a taste, the second is what every planning
decision should be scored against.

The module is the single normalization gate for that model, in the spirit of the
``schemas.PlanDay`` gate in :mod:`services.plan_pipeline`: every write goes
through :func:`normalize_model`, so field invariants — a weight vector that sums
to one, bounded confidences, entries that carry their provenance — hold at the
one place that can enforce them, and no read site has to re-check them.

Two invariants are worth naming because they break silently:

* **Weights always sum to 1.0**, so a utility score is comparable across
  athletes and across time. Pinned components (the athlete fixed them by hand,
  #567) keep their exact value and the remainder is distributed over the rest.
* **``user_set`` beats ``inferred``.** An inference pass (#563) must never
  overwrite an objective the athlete stated by hand — the stale-snapshot clobber
  class the plan pipeline already guards against (#342/#345/#346).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

# The axes a training option is scored on (#564). Fixed rather than free-form:
# the weight vector is only comparable if everyone's vector spans the same space,
# and the planner needs a sub-score per axis it can name in an explanation.
MOTIVATION_COMPONENTS: tuple[str, ...] = (
    "enjoyment",
    "adaptation",
    "consistency",
    "health",
    "race_performance",
)

# The cold-start vector, deliberately *not* the trail-rider example from #561 —
# that is one athlete's answer, not a default for everyone. It leans on
# adaptation because that is what the system optimizes for today, so an athlete
# with no inferred motivation yet keeps getting the recommendations they already
# get. #566 is what moves an athlete off this vector.
DEFAULT_WEIGHTS: dict[str, float] = {
    "enjoyment": 0.25,
    "adaptation": 0.30,
    "consistency": 0.20,
    "health": 0.15,
    "race_performance": 0.10,
}

# Provenance markers, shared with AthleteHomeLocation (#495).
SOURCE_INFERRED = "inferred"
SOURCE_USER_SET = "user_set"
_SOURCES = (SOURCE_INFERRED, SOURCE_USER_SET)

# Entry lifecycle, shared with AthleteMemoryFact (#386): an entry fresh evidence
# argues against goes ``contradicted`` and waits for the athlete rather than
# flipping the objective on its own.
STATUS_ACTIVE = "active"
STATUS_CONTRADICTED = "contradicted"
STATUS_RETIRED = "retired"
_STATUSES = (STATUS_ACTIVE, STATUS_CONTRADICTED, STATUS_RETIRED)

PRIMARY_OBJECTIVE_MAX_LEN = 280
ENTRY_TEXT_MAX_LEN = 200
SNIPPET_MAX_LEN = 500

# Confidence lifecycle for an inferred entry, mirroring the AthleteMemoryFact
# curve in :mod:`crud` so motivation accrues trust the same way every other piece
# of inferred athlete knowledge does. An objective is a big claim: a single
# sentence gets it on the board, and only recurrence gets it believed (#563).
CONFIDENCE_STEP = 0.2
INITIAL_CONFIDENCE_CAP = 0.35
# What a secondary objective must reach before it can be promoted to *the*
# primary objective. Two independent observations plus the step, so one
# offhand remark cannot redefine what the athlete trains for.
PROMOTION_MIN_CONFIDENCE = 0.55
PROMOTION_MIN_OBSERVATIONS = 2

# The most any single evidence batch may move one weight. Behaviour is a slow
# signal by nature: a month of MTB rides in place of prescribed road sessions is
# signal, one swapped Tuesday is noise (#563; the full learning rule is #566).
MAX_WEIGHT_NUDGE = 0.05
# Enough for a real athlete, few enough that the section stays cheap in a prompt
# that is already ~16k tokens (#510/#556).
MAX_SECONDARY_OBJECTIVES = 6
MAX_CONSTRAINTS = 6

_WEIGHT_EPSILON = 1e-9


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _clamp_unit(value: Any, default: float = 0.0) -> float:
    """A float pinned to [0, 1]; ``default`` for anything non-numeric."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return max(0.0, min(1.0, float(value)))


def _clean_text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def _normalize_source(value: Any) -> str:
    return SOURCE_USER_SET if value == SOURCE_USER_SET else SOURCE_INFERRED


def _normalize_status(value: Any) -> str:
    return value if value in _STATUSES else STATUS_ACTIVE


def _iso(value: Any, *, fallback: datetime) -> str:
    """An ISO-8601 timestamp, accepting a datetime, a string, or nothing."""
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return moment.isoformat()
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback.isoformat()


# ---------------------------------------------------------------------------
# Weights
# ---------------------------------------------------------------------------


def normalize_weights(
    raw: Mapping[str, Any] | None,
    *,
    pinned: Iterable[str] | None = None,
) -> dict[str, float]:
    """The utility weight vector, over exactly :data:`MOTIVATION_COMPONENTS`, summing to 1.

    Unknown keys are dropped and missing ones fall back to
    :data:`DEFAULT_WEIGHTS`, so a partial vector from an inference pass is a
    valid input rather than an error.

    Pinned components keep the exact weight the athlete chose; the remaining
    mass is spread over the unpinned ones in proportion to their raw values.
    Learning (#566) must therefore never move a pinned weight, and does not have
    to remember not to — the gate enforces it.
    """
    pinned_set = normalize_pinned(pinned)
    source = raw if isinstance(raw, Mapping) else {}

    values: dict[str, float] = {}
    for component in MOTIVATION_COMPONENTS:
        if component in source:
            values[component] = _clamp_unit(source[component])
        else:
            values[component] = DEFAULT_WEIGHTS[component]

    pinned_mass = sum(values[c] for c in MOTIVATION_COMPONENTS if c in pinned_set)
    unpinned = [c for c in MOTIVATION_COMPONENTS if c not in pinned_set]

    # Degenerate but defined: the athlete pinned more than the whole budget, or
    # pinned everything. Scale the pins down together and zero the rest, rather
    # than silently un-pinning one of their choices.
    if not unpinned or pinned_mass >= 1.0 - _WEIGHT_EPSILON:
        if pinned_mass <= _WEIGHT_EPSILON:
            return {c: DEFAULT_WEIGHTS[c] for c in MOTIVATION_COMPONENTS}
        scaled = {
            c: (values[c] / pinned_mass if c in pinned_set else 0.0)
            for c in MOTIVATION_COMPONENTS
        }
        return _round_to_one(scaled)

    remaining = 1.0 - pinned_mass
    unpinned_mass = sum(values[c] for c in unpinned)
    if unpinned_mass <= _WEIGHT_EPSILON:
        # Nothing to go on: split the remainder evenly instead of leaving a
        # vector that does not sum to one.
        share = remaining / len(unpinned)
        for component in unpinned:
            values[component] = share
    else:
        for component in unpinned:
            values[component] = values[component] / unpinned_mass * remaining

    return _round_to_one(values)


def _round_to_one(values: Mapping[str, float]) -> dict[str, float]:
    """Round to 4 decimals and absorb the rounding error in the largest weight.

    Without this, a stored vector reads as ``0.9999`` and every downstream
    equality check has to carry a tolerance.
    """
    rounded = {c: round(float(values.get(c, 0.0)), 4) for c in MOTIVATION_COMPONENTS}
    drift = round(1.0 - sum(rounded.values()), 4)
    if abs(drift) >= 1e-4:
        largest = max(MOTIVATION_COMPONENTS, key=lambda c: rounded[c])
        rounded[largest] = round(max(0.0, rounded[largest] + drift), 4)
    return rounded


def normalize_pinned(pinned: Iterable[str] | None) -> list[str]:
    """The pinned component names, deduplicated and in canonical order."""
    if not pinned:
        return []
    seen = {p for p in pinned if isinstance(p, str)}
    return [c for c in MOTIVATION_COMPONENTS if c in seen]


# ---------------------------------------------------------------------------
# Objective & constraint entries
# ---------------------------------------------------------------------------


def normalize_entry(raw: Any, *, now: datetime | None = None) -> dict[str, Any] | None:
    """One secondary objective or constraint, with its provenance filled in.

    Returns ``None`` for an entry with no text — an objective nobody can read is
    not an objective. A bare string is accepted so an inference pass can hand up
    ``["stay healthy"]`` without constructing the envelope itself.
    """
    moment = now or _utcnow()

    if isinstance(raw, str):
        raw = {"text": raw}
    if not isinstance(raw, Mapping):
        return None

    text = _clean_text(raw.get("text"), ENTRY_TEXT_MAX_LEN)
    if not text:
        return None

    source = _normalize_source(raw.get("source"))
    # An athlete stating something by hand is not a guess; an inference pass
    # that forgot to score its own confidence gets the same floor a fresh
    # AthleteMemoryFact starts at.
    default_confidence = 1.0 if source == SOURCE_USER_SET else 0.35
    contradiction = _clean_text(raw.get("contradiction_note"), SNIPPET_MAX_LEN)

    observations = raw.get("observation_count")
    if isinstance(observations, bool) or not isinstance(observations, int):
        observations = 1

    return {
        "text": text,
        "confidence": _clamp_unit(raw.get("confidence"), default_confidence),
        "source": source,
        "source_snippet": _clean_text(raw.get("source_snippet"), SNIPPET_MAX_LEN),
        "status": _normalize_status(raw.get("status")),
        "contradiction_note": contradiction or None,
        "observation_count": max(1, observations),
        "first_observed_at": _iso(raw.get("first_observed_at"), fallback=moment),
        "last_confirmed_at": _iso(raw.get("last_confirmed_at"), fallback=moment),
    }


def normalize_entries(
    raw: Sequence[Any] | None,
    *,
    limit: int,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """A list of entries, deduplicated by text and capped at ``limit``.

    Retired entries are dropped: the model is what the athlete is optimizing for
    now, and history belongs in the audit trail (#566), not in the prompt.
    """
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []

    moment = now or _utcnow()
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        entry = normalize_entry(item, now=moment)
        if entry is None or entry["status"] == STATUS_RETIRED:
            continue
        key = entry["text"].casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(entry)

    # A hand-stated objective outranks an inferred one, then confidence. The cap
    # must drop the weakest evidence, not whatever happened to arrive last.
    out.sort(
        key=lambda e: (e["source"] != SOURCE_USER_SET, -float(e["confidence"])),
    )
    return out[:limit]


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def default_model() -> dict[str, Any]:
    """The model an athlete has before anything has been inferred about them.

    Every consumer can rely on getting this shape, so no planner or prompt has
    to special-case an athlete with no row yet.
    """
    return normalize_model({})


def normalize_model(
    raw: Mapping[str, Any] | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The single persist gate: a complete, valid motivation model.

    Accepts a partial or malformed mapping — an LLM inference result, a router
    payload, an ORM row dumped to a dict — and returns the canonical shape with
    every invariant applied.
    """
    moment = now or _utcnow()
    source = raw if isinstance(raw, Mapping) else {}

    primary_source = _normalize_source(source.get("primary_objective_source"))
    primary = _clean_text(source.get("primary_objective"), PRIMARY_OBJECTIVE_MAX_LEN)
    primary_confidence = _clamp_unit(
        source.get("primary_objective_confidence"),
        1.0 if primary_source == SOURCE_USER_SET else 0.35,
    )
    if not primary:
        # No objective means no claim about one: confidence and provenance would
        # otherwise describe an empty string.
        primary_confidence = 0.0
        primary_source = SOURCE_INFERRED

    pinned = normalize_pinned(source.get("pinned_weights"))
    return {
        "primary_objective": primary,
        "primary_objective_source": primary_source,
        "primary_objective_confidence": primary_confidence,
        "primary_objective_snippet": _clean_text(
            source.get("primary_objective_snippet"), SNIPPET_MAX_LEN
        ),
        "secondary_objectives": normalize_entries(
            source.get("secondary_objectives"),
            limit=MAX_SECONDARY_OBJECTIVES,
            now=moment,
        ),
        "constraints": normalize_entries(
            source.get("constraints"), limit=MAX_CONSTRAINTS, now=moment
        ),
        "utility_weights": normalize_weights(
            source.get("utility_weights"), pinned=pinned
        ),
        "pinned_weights": pinned,
    }


def merge_model(
    existing: Mapping[str, Any] | None,
    incoming: Mapping[str, Any] | None,
    *,
    source: str = SOURCE_INFERRED,
    accrue: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fold an update into the stored model, honouring the user-set override.

    An ``inferred`` write — an inference pass (#563), a weight-learning run
    (#566) — leaves anything the athlete set by hand exactly as it was: the
    primary objective if they wrote it, every ``user_set`` entry, and every
    pinned weight. A ``user_set`` write always wins.

    This is why inference callers do not need their own guard: they cannot
    clobber an athlete's stated objective even if they try.

    ``accrue`` switches entry merging from *replace* to *observe*: a repeated
    inferred entry gains :data:`CONFIDENCE_STEP` and an observation instead of
    taking the incoming confidence outright. That is what makes an objective
    something the athlete has to keep demonstrating before the coach acts on it
    (#563), as opposed to something one sentence can assert.
    """
    moment = now or _utcnow()
    current = normalize_model(existing, now=moment)
    update = normalize_model(incoming, now=moment)
    writing_as_user = _normalize_source(source) == SOURCE_USER_SET

    # A field the caller did not send is a field it has nothing to say about —
    # distinct from an empty one, which for a ``user_set`` write means "remove
    # these". Without the distinction a partial edit ("just fix my primary
    # objective") would blank every list the athlete left alone.
    provided = set(incoming.keys()) if isinstance(incoming, Mapping) else set()

    merged = dict(current)

    # --- primary objective -------------------------------------------------
    primary_is_protected = (
        current["primary_objective"]
        and current["primary_objective_source"] == SOURCE_USER_SET
        and not writing_as_user
    )
    # An athlete may clear their objective; an inference pass may not — a blank
    # from the model is noise, not a decision to stop having a goal.
    primary_is_writable = "primary_objective" in provided and (
        writing_as_user or update["primary_objective"]
    )
    if primary_is_writable and not primary_is_protected:
        merged["primary_objective"] = update["primary_objective"]
        merged["primary_objective_source"] = (
            SOURCE_USER_SET if writing_as_user else update["primary_objective_source"]
        )
        merged["primary_objective_confidence"] = (
            1.0 if writing_as_user else update["primary_objective_confidence"]
        )
        merged["primary_objective_snippet"] = update["primary_objective_snippet"]

    # --- entries -----------------------------------------------------------
    for field, limit in (
        ("secondary_objectives", MAX_SECONDARY_OBJECTIVES),
        ("constraints", MAX_CONSTRAINTS),
    ):
        if field not in provided:
            continue
        merged[field] = _merge_entries(
            current[field],
            update[field],
            limit=limit,
            writing_as_user=writing_as_user,
            accrue=accrue,
            now=moment,
        )

    # --- weights -----------------------------------------------------------
    if writing_as_user and "pinned_weights" in provided:
        pinned = update["pinned_weights"]
    else:
        pinned = current["pinned_weights"]

    if "utility_weights" in provided:
        candidate = dict(update["utility_weights"])
        if not writing_as_user:
            # Pinned components are the athlete's, not the learner's.
            for component in pinned:
                candidate[component] = current["utility_weights"][component]
    else:
        candidate = dict(current["utility_weights"])

    merged["utility_weights"] = normalize_weights(candidate, pinned=pinned)
    merged["pinned_weights"] = pinned

    return merged


def _merge_entries(
    current: list[dict[str, Any]],
    update: list[dict[str, Any]],
    *,
    limit: int,
    writing_as_user: bool,
    accrue: bool,
    now: datetime,
) -> list[dict[str, Any]]:
    """Entry-wise merge, keyed on text.

    A user-set entry survives an inferred write; a repeated observation keeps its
    original ``first_observed_at`` so a long-standing objective can be told from
    one stated once.
    """
    if writing_as_user:
        # The athlete is restating the whole list — including by omission.
        return normalize_entries(update, limit=limit, now=now)

    by_key = {entry["text"].casefold(): dict(entry) for entry in current}
    for entry in update:
        key = entry["text"].casefold()
        previous = by_key.get(key)
        if previous is None:
            new_entry = dict(entry)
            if accrue:
                # A first sighting is a candidate, not knowledge: it enters below
                # the bar its own recurrence has to carry it over.
                new_entry["confidence"] = min(
                    new_entry["confidence"], INITIAL_CONFIDENCE_CAP
                )
            by_key[key] = new_entry
            continue
        if previous["source"] == SOURCE_USER_SET:
            # Re-observing what the athlete already told us is confirmation, not
            # a correction: refresh recency, keep their wording and authority.
            previous["last_confirmed_at"] = entry["last_confirmed_at"]
            previous["observation_count"] = previous.get("observation_count", 1) + 1
            continue

        first_seen = previous.get("first_observed_at") or entry["first_observed_at"]
        observations = previous.get("observation_count", 1)
        held = previous["confidence"]
        contradiction = previous.get("contradiction_note")
        previous.update(entry)
        previous["first_observed_at"] = min(first_seen, entry["first_observed_at"])

        if accrue:
            previous["observation_count"] = observations + 1
            previous["confidence"] = min(
                1.0, max(held, min(entry["confidence"], INITIAL_CONFIDENCE_CAP))
                + CONFIDENCE_STEP,
            )
            # Fresh evidence re-confirms the entry, so a pending contradiction is
            # answered and the validation prompt clears — the same resolution
            # AthleteMemoryFact applies when a contradicted fact recurs.
            previous["status"] = STATUS_ACTIVE
            previous["contradiction_note"] = None
        else:
            previous["observation_count"] = max(
                observations, previous.get("observation_count", 1)
            )
            previous["contradiction_note"] = (
                previous.get("contradiction_note") or contradiction
            )
    return normalize_entries(list(by_key.values()), limit=limit, now=now)


# ---------------------------------------------------------------------------
# Evidence: promotion, contradiction, bounded weight movement
# ---------------------------------------------------------------------------


def promote_primary_objective(
    model: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Raise the best-evidenced secondary objective to primary, if one qualifies.

    An athlete's primary objective is the single thing every planning decision is
    scored against, so it is not something a passing remark gets to set. A
    candidate has to clear both bars — :data:`PROMOTION_MIN_CONFIDENCE` and
    :data:`PROMOTION_MIN_OBSERVATIONS` — which under the accrual curve means the
    athlete said or demonstrated it more than once.

    A ``user_set`` primary objective is never replaced: the athlete already
    answered this question.
    """
    moment = now or _utcnow()
    current = normalize_model(model, now=moment)

    if current["primary_objective"] and (
        current["primary_objective_source"] == SOURCE_USER_SET
    ):
        return current

    qualified = [
        entry
        for entry in current["secondary_objectives"]
        if entry["status"] == STATUS_ACTIVE
        and entry["confidence"] >= PROMOTION_MIN_CONFIDENCE
        and entry["observation_count"] >= PROMOTION_MIN_OBSERVATIONS
    ]
    if not qualified:
        return current

    best = max(
        qualified,
        key=lambda e: (e["confidence"], e["observation_count"]),
    )
    if best["confidence"] <= current["primary_objective_confidence"]:
        return current

    promoted = dict(current)
    promoted["primary_objective"] = best["text"][:PRIMARY_OBJECTIVE_MAX_LEN]
    promoted["primary_objective_source"] = best["source"]
    promoted["primary_objective_confidence"] = best["confidence"]
    promoted["primary_objective_snippet"] = best["source_snippet"]
    # The promoted objective stops being one of the also-rans; a demoted former
    # primary is not re-added, because it was never evidence in its own right.
    promoted["secondary_objectives"] = [
        entry
        for entry in current["secondary_objectives"]
        if entry["text"].casefold() != best["text"].casefold()
    ]
    return promoted


def contradict_entry(
    entry: Mapping[str, Any],
    *,
    note: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Flag an entry that fresh evidence argues against.

    Deliberately not a deletion. "I don't care about races" alongside three
    registered races is a question for the athlete, not a fact the coach gets to
    overrule on its own — so the entry keeps its text, loses confidence, and
    carries the reason into the UI where the athlete can confirm or correct it
    (#567). This is the resolution path :class:`AthleteMemoryFact` already uses.
    """
    flagged = normalize_entry(entry, now=now)
    if flagged is None:
        return {}
    flagged["status"] = STATUS_CONTRADICTED
    flagged["contradiction_note"] = _clean_text(note, SNIPPET_MAX_LEN) or None
    flagged["confidence"] = _clamp_unit(flagged["confidence"] - CONFIDENCE_STEP)
    return flagged


def apply_weight_evidence(
    weights: Mapping[str, Any] | None,
    nudges: Mapping[str, float] | None,
    *,
    pinned: Iterable[str] | None = None,
    cap: float = MAX_WEIGHT_NUDGE,
) -> dict[str, float]:
    """Move the weight vector by a bounded amount of evidence.

    Each nudge is clamped to ±``cap`` before it is applied, so no single batch of
    evidence can rewrite what the athlete values — it takes a signal that repeats
    across runs to move the vector meaningfully. Pinned components ignore their
    nudge entirely, and the result is renormalized through
    :func:`normalize_weights`, so the sum-to-one invariant holds by construction
    rather than by the caller remembering.
    """
    pinned_set = set(normalize_pinned(pinned))
    current = normalize_weights(weights, pinned=pinned)
    if not nudges:
        return current

    moved = dict(current)
    for component, delta in nudges.items():
        if component not in MOTIVATION_COMPONENTS or component in pinned_set:
            continue
        if isinstance(delta, bool) or not isinstance(delta, (int, float)):
            continue
        bounded = max(-cap, min(cap, float(delta)))
        moved[component] = _clamp_unit(moved[component] + bounded)

    return normalize_weights(moved, pinned=pinned)
