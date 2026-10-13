"""The power-duration curve of one ride, for the public FIT tool (ai-trainer-ops#25).

Pure arithmetic on a power stream: no database, no account, no model call. The
tool is an entrance that must cost nothing to serve, so nothing here may grow a
dependency on either.

Two things come out:

*The ride's mean-maximal power* at standard durations, read straight from the
stream with :func:`services.analysis.best_n_min_power`.

*A two-parameter critical-power fit* over the 2-20 minute points, with its
uncertainty. The model is ``P(t) = CP + W'/t``, fitted as the linear
``work = CP * t + W'``. The band is two standard errors from the regression
residuals, so a ride whose efforts do not line up with the model gets a wide
band instead of a confident wrong number.
"""

from __future__ import annotations

import math

from services.analysis import best_n_min_power

DURATIONS_SECONDS = (
    5, 15, 30, 60, 120, 180, 300, 480, 600, 720, 1200, 1800, 2400, 3600, 5400, 7200,
)
# The range where the two-parameter model holds. Shorter is anaerobic-power
# limited, longer is fatigue the model does not have.
FIT_MIN_SECONDS = 120
FIT_MAX_SECONDS = 1200
# ponytail: 2 standard errors as a ~95% band; a t-quantile would be wider for
# the 3-6 points a single ride gives, use it if the band is read as a promise.
BAND_SE = 2.0


def mean_maximal_power(
    watts: list[float], times: list[float]
) -> list[dict[str, int]]:
    """Best average power for each standard duration the ride is long enough for."""
    points = []
    for seconds in DURATIONS_SECONDS:
        best, _, _ = best_n_min_power(watts, times, seconds / 60.0)
        if best is not None:
            points.append({"seconds": seconds, "watts": round(best)})
    return points


def fit_critical_power(points: list[dict[str, int]]) -> dict | None:
    """CP and W' with standard errors, or ``None`` when the ride cannot support one.

    ``None`` for fewer than three points in range, and for a flat curve: a ride
    without a hard short effort describes pacing, not capacity, and a fit
    through it would report endurance power as CP.
    """
    used = [p for p in points if FIT_MIN_SECONDS <= p["seconds"] <= FIT_MAX_SECONDS]
    if len(used) < 3:
        return None
    if used[0]["watts"] < used[-1]["watts"] * 1.05:
        return None

    xs = [float(p["seconds"]) for p in used]
    ys = [float(p["watts"] * p["seconds"]) for p in used]
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    cp = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / sxx
    w_prime = mean_y - cp * mean_x
    if cp <= 0 or w_prime <= 0:
        return None

    residual = sum((y - (cp * x + w_prime)) ** 2 for x, y in zip(xs, ys))
    s2 = residual / (n - 2) if n > 2 else 0.0
    return {
        "cp": cp,
        "w_prime": w_prime,
        "var_cp": s2 / sxx,
        "var_w": s2 * (1.0 / n + mean_x**2 / sxx),
        "cov": -mean_x * s2 / sxx,
        "points_used": n,
    }


def model_curve(fit: dict) -> list[dict[str, int]]:
    """``P(t)`` with its band at each fitted-range duration."""
    curve = []
    for seconds in DURATIONS_SECONDS:
        if not FIT_MIN_SECONDS <= seconds <= FIT_MAX_SECONDS:
            continue
        t = float(seconds)
        watts = fit["cp"] + fit["w_prime"] / t
        variance = fit["var_cp"] + fit["var_w"] / t**2 + 2 * fit["cov"] / t
        spread = BAND_SE * math.sqrt(max(variance, 0.0))
        curve.append(
            {
                "seconds": seconds,
                "watts": round(watts),
                "low": round(watts - spread),
                "high": round(watts + spread),
            }
        )
    return curve


def power_duration(watts: list[float], times: list[float]) -> dict:
    """Everything the tool shows for one ride."""
    points = mean_maximal_power(watts, times)
    fit = fit_critical_power(points)
    if fit is None:
        return {"points": points, "model": None, "curve": []}
    cp_spread = BAND_SE * math.sqrt(fit["var_cp"])
    w_spread = BAND_SE * math.sqrt(fit["var_w"])
    return {
        "points": points,
        "model": {
            "cp": round(fit["cp"]),
            "cp_low": round(fit["cp"] - cp_spread),
            "cp_high": round(fit["cp"] + cp_spread),
            "w_prime": round(fit["w_prime"]),
            "w_prime_low": round(fit["w_prime"] - w_spread),
            "w_prime_high": round(fit["w_prime"] + w_spread),
            "points_used": fit["points_used"],
        },
        "curve": model_curve(fit),
    }
