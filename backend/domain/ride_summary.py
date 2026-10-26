"""A stored ride's one-line summary, written without a model (ai-trainer-ops#304).

The analysis writes it for a new ride, and crud for a ride whose figures it
corrects, so it lives below both.
"""

from __future__ import annotations

from domain.training_load import format_load


def build_rule_based_summary(
    ride_purpose: str,
    duration_seconds: float,
    normalized_power: float | None,
    tss: float | None,
    intervals: list[dict],
    *,
    tss_source: str | None = None,
) -> str:
    """Build a short deterministic 1-line ride summary — no LLM required.

    Examples:
    - "Threshold intervals: 3×10 min @ 275 W · TSS 94 · 1h20m"
    - "Endurance: NP 198 W · TSS 61 · 2h05m"
    - "Strength · load ~34 (estimated from HR, medium confidence) · 58m"
    - "Recovery: 45 min"

    ``tss_source`` decides whether the load is printed as a measured figure or
    as the estimate it is; see :func:`domain.training_load.format_load`.
    """
    h = int(duration_seconds // 3600)
    m = int((duration_seconds % 3600) // 60)
    duration_str = f"{h}h{m:02d}m" if h > 0 else f"{m}m"

    label_map = {
        "unknown": "Unknown ride",
        "short_easy_spin": "Short easy spin",
        "short_hard_effort": "Short hard effort",
        "recovery": "Recovery",
        "endurance": "Endurance",
        "tempo": "Tempo",
        "interval_sweetspot": "Sweet-spot intervals",
        "interval_threshold": "Threshold intervals",
        "interval_vo2max": "VO2max intervals",
        "interval_sprints": "Sprint intervals",
        "mixed": "Mixed intervals",
    }
    label = label_map.get(ride_purpose, ride_purpose.replace("_", " ").capitalize())

    parts = [label]

    # Interval summary: count × duration @ avg_power
    if intervals and ride_purpose not in ("recovery", "endurance", "tempo"):
        reps = len(intervals)
        avg_dur_min = round(sum(iv["duration_secs"] for iv in intervals) / reps / 60)
        avg_power = round(sum(iv["avg_power"] for iv in intervals) / reps)
        parts[0] = f"{label}: {reps}×{avg_dur_min} min @ {avg_power} W"
    elif normalized_power:
        parts.append(f"NP {round(normalized_power)} W")

    load_text = format_load(tss, tss_source)
    if load_text is not None:
        parts.append(load_text)

    parts.append(duration_str)
    return " · ".join(parts)
