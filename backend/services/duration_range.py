"""Duration-range helpers for planned activities (#368).

A planned activity may prescribe a duration *window* (e.g. 2.5–3h endurance)
rather than a single fixed value. We store the window on a plan day as
``durationMinMinutes`` / ``durationMaxMinutes``; a single value is simply the
degenerate window where ``min == max``.

The legacy scalar ``durationMinutes`` is kept populated (the window midpoint)
so the many existing readers that expect one number — TSS/load estimates,
ride matching, chat context — keep working without change. Callers that care
about "did the athlete hit the plan?" should use :func:`duration_on_target`,
which treats any actual duration inside the window as on-target.

Day dicts in this codebase are stored with camelCase keys (plan days are
dumped ``by_alias``), but a few code paths still carry snake_case, so the
readers here accept either.
"""

from __future__ import annotations


def _as_positive_int(value: object) -> int | None:
    """Coerce ``value`` to a positive int, or ``None`` when not usable."""
    try:
        result = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _explicit_bounds(day: dict) -> tuple[int | None, int | None]:
    """Return the explicit (min, max) bounds carried on ``day``, if any."""
    lo = _as_positive_int(
        day.get("durationMinMinutes")
        if day.get("durationMinMinutes") is not None
        else day.get("duration_min_minutes")
    )
    hi = _as_positive_int(
        day.get("durationMaxMinutes")
        if day.get("durationMaxMinutes") is not None
        else day.get("duration_max_minutes")
    )
    return lo, hi


def _scalar_minutes(day: dict) -> int | None:
    return _as_positive_int(
        day.get("durationMinutes")
        if day.get("durationMinutes") is not None
        else day.get("duration_minutes")
    )


def duration_range(day: dict | None) -> tuple[int | None, int | None]:
    """Return the planned duration window ``(lo, hi)`` for ``day``.

    A missing bound falls back to the other bound and then to the scalar
    ``durationMinutes``, so a single-value day reads back as ``(v, v)``. The
    bounds are ordered. Returns ``(None, None)`` when the day has no usable
    duration (e.g. a rest day).
    """
    if not isinstance(day, dict):
        return None, None
    lo_e, hi_e = _explicit_bounds(day)
    scalar = _scalar_minutes(day)
    # A missing bound is the scalar (the window's other edge) when one exists,
    # otherwise it mirrors the given bound (a single value → degenerate window).
    lo = lo_e if lo_e is not None else (scalar if scalar is not None else hi_e)
    hi = hi_e if hi_e is not None else (scalar if scalar is not None else lo_e)
    if lo is None or hi is None:
        return None, None
    return (lo, hi) if lo <= hi else (hi, lo)


def reconcile_duration(
    scalar: object, lo: object, hi: object
) -> tuple[int | None, int | None]:
    """Return coherent ``(min, max)`` bounds from a scalar + optional window.

    Shared arithmetic behind both :func:`normalize_duration_fields` (dict form)
    and the ``PlanDay`` model validator (typed form), so the scalar↔window
    invariant lives in one place. Non-positive/None inputs count as absent.

    - No explicit bound present → ``(None, None)`` (a single-value / rest day;
      the caller keeps its scalar untouched).
    - A window is present → the bounds are filled from the scalar/other bound
      and ordered; the caller derives the scalar as ``round(midpoint)``.
    """
    lo_e = _as_positive_int(lo)
    hi_e = _as_positive_int(hi)
    if lo_e is None and hi_e is None:
        return None, None
    s = _as_positive_int(scalar)
    lo2 = lo_e if lo_e is not None else (s if s is not None else hi_e)
    hi2 = hi_e if hi_e is not None else (s if s is not None else lo_e)
    assert lo2 is not None and hi2 is not None  # at least one explicit bound
    return (lo2, hi2) if lo2 <= hi2 else (hi2, lo2)


def representative_minutes(day: dict | None) -> int | None:
    """A single representative duration for ``day`` — the window midpoint."""
    lo, hi = duration_range(day)
    if lo is None or hi is None:
        return None
    return round((lo + hi) / 2)


def has_range(day: dict | None) -> bool:
    """Whether ``day`` prescribes a genuine window (``min != max``)."""
    lo, hi = duration_range(day)
    return lo is not None and hi is not None and lo != hi


def duration_on_target(day: dict | None, actual_minutes: float | None) -> bool | None:
    """Whether ``actual_minutes`` falls within the planned window for ``day``.

    Returns ``None`` when there is nothing to compare (no planned duration or
    no actual). Any value inside ``[lo, hi]`` (inclusive) counts as on-target.
    """
    if actual_minutes is None:
        return None
    lo, hi = duration_range(day)
    if lo is None or hi is None:
        return None
    return lo <= actual_minutes <= hi


def normalize_duration_fields(day: dict) -> dict:
    """Return ``day`` with consistent duration fields when a window is present.

    Single-value days (no explicit min/max) are returned unchanged so legacy
    plans do not churn. When a window *is* prescribed, the bounds are ordered
    and the scalar ``durationMinutes`` is set to the midpoint (derived), so
    every reader sees a coherent view. Snake_case duplicates are dropped in
    favour of the canonical camelCase keys.
    """
    if not isinstance(day, dict):
        return day
    lo, hi = reconcile_duration(
        _scalar_minutes(day), *_explicit_bounds(day)
    )
    if lo is None and hi is None:
        # No window intent — leave the single-value day exactly as-is.
        return day
    normalized = {
        k: v
        for k, v in day.items()
        if k
        not in {
            "durationMinutes",
            "duration_minutes",
            "durationMinMinutes",
            "duration_min_minutes",
            "durationMaxMinutes",
            "duration_max_minutes",
        }
    }
    normalized["durationMinMinutes"] = lo
    normalized["durationMaxMinutes"] = hi
    normalized["durationMinutes"] = round((lo + hi) / 2)
    return normalized
