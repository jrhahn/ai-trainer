"""Deterministic, per-session coaching hypotheses (#479, Level 1).

Where :mod:`services.hypothesis_generation` asks the LLM once a week for free-form
ideas, this engine forms hypotheses **automatically after every session** straight
from the deterministic Athlete Performance Model (#476) and its detected limiter
(#477) — no LLM, no prompt. Each hypothesis is a tentative, testable claim that
carries, per the epic's cross-cutting principle:

* ``evidence`` — the concrete numbers behind it (FTP vs MAP, fractional
  utilization, endurance/durability scores),
* ``confidence`` — inherited from the underlying limiter/attribute, never a guess,
* ``alternative_explanations`` — the competing readings the coach must still rule
  out before trusting the claim.

The pure :func:`derive_performance_hypotheses` maps the model to that list; the
async :func:`refresh_performance_hypotheses` persists them through the existing
merge/decay lifecycle (:func:`crud.propose_athlete_hypothesis`) so a recurring
hypothesis accrues evidence and confidence instead of duplicating, while one the
model no longer supports decays and is eventually retired
(:func:`crud.decay_unsupported_model_hypotheses`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
from services.limiter_detection import (
    LIMITER_DURABILITY,
    LIMITER_THRESHOLD,
    LIMITER_VO2MAX,
    top_limiter,
)

# All deterministic performance-model hypotheses share one category so they can be
# reconciled together (decayed/retired) without touching LLM-formed hypotheses.
CATEGORY = "performance_model"

# A hypothesis is only formed when the signal behind it is at least this confident.
# Below the gate there is not enough evidence to assert even a tentative claim.
_CONF_GATE = 0.35

# Endurance/durability scores strong enough to back a "strong aerobic base" claim.
_STRONG_SCORES = {"high", "above_average"}
# Fractional utilization below which FTP looks low against the aerobic ceiling.
_FTP_UNDERESTIMATE_FRAC = 0.70


def _num(attr: dict | None) -> float | None:
    if not attr:
        return None
    est = attr.get("estimate")
    return float(est) if isinstance(est, (int, float)) else None


def _score(attr: dict | None) -> str | None:
    if not attr:
        return None
    score = attr.get("score")
    return score if isinstance(score, str) and score != "unknown" else None


def _conf(attr: dict | None) -> float:
    if not attr:
        return 0.0
    raw = attr.get("confidence")
    return float(raw) if isinstance(raw, (int, float)) else 0.0


def _limiter_confidence(limiters: list[dict], limiter: str) -> float:
    for cand in limiters:
        if cand.get("limiter") == limiter:
            raw = cand.get("confidence")
            return float(raw) if isinstance(raw, (int, float)) else 0.0
    return 0.0


def _hypothesis(
    statement: str,
    confidence: float,
    evidence: list[str],
    alternatives: list[str],
) -> dict[str, Any]:
    return {
        "statement": statement,
        "category": CATEGORY,
        "confidence": round(max(0.0, min(1.0, confidence)), 2),
        # A one-line human summary for the existing rationale column / older views.
        "rationale": " ".join(evidence),
        "evidence": evidence,
        "alternative_explanations": alternatives,
    }


def _limiter_hypothesis(
    limiter: str, confidence: float, attrs: dict[str, dict]
) -> dict[str, Any] | None:
    ftp = _num(attrs.get("ftp"))
    mp = _num(attrs.get("map"))
    frac = _num(attrs.get("fractional_utilization"))
    aer = _score(attrs.get("aerobic_endurance"))
    fat = _score(attrs.get("fatigue_resistance"))

    if limiter == LIMITER_THRESHOLD:
        evidence: list[str] = []
        if ftp and mp:
            evidence.append(
                f"Sustainable threshold (~{ftp:g} W) sits well below maximal aerobic "
                f"power (~{mp:g} W)"
                + (f", only {frac:.0%} of the ceiling." if frac else ".")
            )
        if aer:
            evidence.append(f"Aerobic endurance scores '{aer}', a base to build on.")
        if not evidence:
            return None
        return _hypothesis(
            "The athlete's current limiter is likely threshold utilization — "
            "sustainable power lags behind the aerobic ceiling.",
            confidence,
            evidence,
            [
                "The MAP estimate may be inflated by a single short maximal effort.",
                "Durability, not threshold, could be the true limiter if power fades "
                "late in long rides.",
            ],
        )

    if limiter == LIMITER_VO2MAX:
        evidence = []
        if ftp and mp:
            evidence.append(
                f"Threshold (~{ftp:g} W) already sits close to maximal aerobic power "
                f"(~{mp:g} W)"
                + (f", about {frac:.0%} of it." if frac else ".")
            )
        if not evidence:
            return None
        return _hypothesis(
            "VO₂max is likely the current ceiling — threshold already sits close to "
            "maximal aerobic power, leaving little room for more threshold work.",
            confidence,
            evidence,
            [
                "Threshold may be underestimated, making the gap look smaller than it is.",
                "A longer aerobic base block could still yield durability gains.",
            ],
        )

    if limiter == LIMITER_DURABILITY:
        evidence = []
        if fat:
            evidence.append(
                f"Fatigue resistance scores '{fat}' — power drops in the second half "
                "of long rides."
            )
        if aer:
            evidence.append(f"Aerobic endurance scores '{aer}'.")
        if not evidence:
            return None
        return _hypothesis(
            "Aerobic durability is likely the current limiter — power fades late in "
            "long rides rather than at the top end.",
            confidence,
            evidence,
            [
                "Recent long rides may have been paced too hard, exaggerating the "
                "late-ride fade.",
                "The fade could reflect fuelling rather than a physiological "
                "durability ceiling.",
            ],
        )
    return None


def derive_performance_hypotheses(
    attributes: dict[str, dict] | None,
    limiters: list[dict] | None,
) -> list[dict[str, Any]]:
    """Map the performance model + detected limiter to testable hypotheses.

    ``attributes`` is the Athlete Performance Model attribute map and ``limiters``
    the ranked list from :func:`services.limiter_detection.detect_limiters`. Returns
    a list of hypothesis dicts (``statement``/``category``/``confidence``/
    ``rationale``/``evidence``/``alternative_explanations``), strongest first, or an
    empty list when the model has no signal confident enough to assert a claim.
    """
    attrs = attributes or {}
    limiters = limiters or []
    hypotheses: list[dict[str, Any]] = []

    limiter = top_limiter(limiters)
    if limiter is not None:
        confidence = _limiter_confidence(limiters, limiter)
        if confidence >= _CONF_GATE:
            limiter_hyp = _limiter_hypothesis(limiter, confidence, attrs)
            if limiter_hyp is not None:
                hypotheses.append(limiter_hyp)

    # A strong aerobic engine is a standalone, positive hypothesis worth surfacing.
    aer_attr = attrs.get("aerobic_endurance")
    aer_score = _score(aer_attr)
    if aer_score in _STRONG_SCORES and _conf(aer_attr) >= _CONF_GATE:
        hypotheses.append(
            _hypothesis(
                "The athlete has developed a strong aerobic engine.",
                _conf(aer_attr),
                [
                    f"Aerobic endurance scores '{aer_score}' across recent long rides "
                    "with low cardiac drift."
                ],
                [
                    "The long rides may not have been long enough to expose a "
                    "durability limit.",
                ],
            )
        )

    # FTP looking low against a strong aerobic ceiling is a distinct, testable claim.
    frac_attr = attrs.get("fractional_utilization")
    frac = _num(frac_attr)
    if (
        frac is not None
        and frac < _FTP_UNDERESTIMATE_FRAC
        and aer_score in _STRONG_SCORES
        and _conf(frac_attr) >= _CONF_GATE
    ):
        hypotheses.append(
            _hypothesis(
                "FTP could be underestimated relative to the aerobic ceiling.",
                _conf(frac_attr),
                [
                    f"Fractional utilization is only {frac:.0%}, low for an athlete "
                    f"with a '{aer_score}' aerobic base.",
                ],
                [
                    "The aerobic ceiling (MAP/VO₂max) may be overestimated instead.",
                    "The athlete may be genuinely threshold-limited and simply need "
                    "more threshold work.",
                ],
            )
        )

    hypotheses.sort(key=lambda h: h["confidence"], reverse=True)
    return hypotheses


async def refresh_performance_hypotheses(
    db: AsyncSession,
    user: models.User,
    *,
    now: datetime | None = None,
) -> int:
    """Regenerate and persist the athlete's deterministic hypotheses (#479).

    Best-effort and side-effect-safe: reads the stored performance model, derives
    the current hypotheses, reinforces each through the merge lifecycle, then decays
    any previously proposed model hypothesis the model no longer supports. Writes
    are flushed but not committed — the caller owns the transaction, matching the
    continuous-learning pipeline. Returns the number of hypotheses asserted.
    """
    model = await crud.get_athlete_performance_model(db, user.id)
    if model is None:
        return 0

    derived = derive_performance_hypotheses(model.attributes, model.limiters)

    supported: set[str] = set()
    for hyp in derived:
        row = await crud.propose_athlete_hypothesis(
            db,
            user.id,
            statement=hyp["statement"],
            category=hyp["category"],
            rationale=hyp["rationale"],
            confidence=hyp["confidence"],
            evidence=hyp["evidence"],
            alternative_explanations=hyp["alternative_explanations"],
            observed_at=now,
        )
        supported.add(row.statement_key)

    await crud.decay_unsupported_model_hypotheses(
        db,
        user.id,
        category=CATEGORY,
        supported_keys=supported,
        now=now,
    )
    return len(derived)
