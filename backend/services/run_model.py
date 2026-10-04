"""Running with its own performance model, not a cycling one in other units (#716).

Running has been in this app as a *quantity of fatigue* and nothing else. #712
gave it a load figure from heart rate or from an assumed cost per hour, and #713
stopped that figure being banked as cycling fitness — but neither says anything
about how fast the athlete is, or what pace is hard for them. The coach has
therefore been able to see that a run happened and how tired it left the athlete,
and nothing in between.

The temptation is to borrow the cycling model, and #711 is the record of why that
fails: a run's watts are a different quantity from a cyclist's watts, and every
number cut from a cycling FTP — the zones, the intensity factor, the
power–duration envelope — is about a threshold the run never approached. The
fitting *machinery* transfers; the watt assumptions baked around it do not.

So this module is the running analogue, built from the running literature:

``critical_speed_from_points`` — Critical Speed and D′ from the pace–duration
envelope, by the hyperbolic speed–duration relationship (Monod & Scherrer 1965;
Hill 1993; Jones & Vanhatalo 2017). The same linear fit as
``analysis._critical_power_from_points`` reads distance where that one reads
work, and it keeps the intercept the cycling fit discards: for a cyclist W′ is a
secondary curiosity next to CP, while for a runner D′ *is* the kick, the gap
between a 1500 m and a 5000 m athlete, and the thing a 400 m repeat session
trains.

``grade_cost_factor`` — grade-adjusted pace from Minetti et al. (2002), the
measured metabolic cost of running on a gradient. Without it a hill run and a
flat run are not comparable, and the athlete's own hilly loop looks like a bad
day every time.

``run_training_load`` — rTSS, the running sport-specific load tier. It sits
*above* heart rate in ``services.training_load``'s ladder, because pace against
a known threshold pace is a measurement where hrTSS is an inference from a
response that drifts with heat, caffeine and sleep.

Two things are deliberately absent.

**Running power is never computed here.** Stryd and the GOVSS family (Skiba)
model it from pace, mass and gradient — all of which this module already has —
and the arithmetic would be easy. It is still wrong to do: a modelled watt that
looks like a measured one is exactly the conflation #579 and #712 spent two
issues separating, and the provider-first rule for lossy streams (#466) says the
same thing about a number we could recompute but should not. A provider-reported
running power is welcome as an input; one of ours would be a guess wearing a
unit.

**Nothing here decides what the athlete should run.** The model answers "how fast
is this athlete, how hard was this run" and stops. Prescription reads it.

Pure module — no DB, no HTTP, no LLM — so the chain, the inference engine and
the prompt all price a run by the same rule instead of three.
"""

from __future__ import annotations

from dataclasses import dataclass

# Durations probed on the pace–duration envelope, in minutes. Shorter at the
# bottom than the cycling set (``analysis.PERF_SIGNAL_DURATIONS_MIN``) because a
# runner's envelope has to reach into the D′ region — a 2-minute effort is most
# of what pins the intercept — and no deeper than 90 min at the top, since a
# long run is a different question from a maximal one.
RUN_SIGNAL_DURATIONS_MIN: tuple[float, ...] = (
    1,
    2,
    3,
    5,
    8,
    10,
    15,
    20,
    30,
    45,
    60,
    90,
)

# Which of those points the Critical Speed fit is allowed to use. The
# speed–duration relationship is hyperbolic between roughly 2 and 15-20 minutes
# and departs from it outside that band: below 2 min the effort is largely
# anaerobic and above ~20 min the aerobic decline the model does not describe
# starts to bend the curve down (Jones & Vanhatalo). A fit over 1 min and 90 min
# would return a number for a relationship that does not hold there.
CRITICAL_SPEED_DURATIONS: tuple[float, ...] = (2, 3, 5, 8, 10, 15, 20)

#: Fewest envelope points the fit will accept. Three is the classic protocol.
MIN_CS_POINTS = 3

# How far apart the shortest and longest effort must sit, in minutes. Two points
# three minutes apart carry almost no information about the intercept, and the
# fit would hand back a D′ invented by rounding error.
MIN_CS_SPAN_MINUTES = 8.0

# The envelope has to actually slope down. A runner who held the same pace for
# 3 minutes as for 20 did not run a maximal 3 minutes, and fitting that curve
# puts CS *above* threshold — the error that makes every subsequent rTSS too
# small and every zone too fast. Flatter than the cycling equivalent (1.05)
# because speed compresses where power does not: the 3-to-20-minute speed ratio
# of a trained runner is around 1.10 where the power ratio is near 1.35.
MIN_CS_CURVE_DECLINE = 1.03

# How far a measured point may sit from the fitted hyperbola, as a fraction of
# its speed. This is the test that the model *fits*, and it is applied in speed
# space on purpose: the usual r² is computed on the linearised distance-time
# form, where distance ≈ speed × time is nearly collinear by construction and
# r² comes out above 0,99 for curves the model does not describe at all. A
# 5 % residual in speed is ~12 s/km at 4:00 pace, which an athlete would notice.
MAX_CS_SPEED_RESIDUAL = 0.05

# D′ is the finite distance available above Critical Speed, in metres. Trained
# runners sit around 100-300 m, middle-distance specialists higher. Outside this
# the fit has not found a physiological quantity: a D′ of 2 km would mean the
# athlete can run 2 km at any speed at all, and a D′ near zero means the two
# ends of the curve agreed, which is the flat-curve case again seen from the
# other side.
D_PRIME_MIN_M = 50.0
D_PRIME_MAX_M = 1000.0

# CS must land below the slowest maximal effort in the fit and not far below it.
# A 20-minute maximal effort is run a few percent above CS, so a CS under 80 %
# of the longest effort's speed means the fit extrapolated rather than
# interpolated.
CS_LOWER_BOUND_OF_LONGEST = 0.80

# Threshold pace as a fraction of Critical Speed. The two are close but not the
# same thing, and the difference is the hour the rTSS scale is anchored to: CS
# is the upper boundary of the maximal metabolic steady state and is held for
# roughly 30 minutes in practice, where functional threshold pace is the pace a
# runner can hold for about an hour. Treating them as equal would make every
# rTSS figure about 8 % too small — small enough to look plausible, large
# enough to mis-plan a build.
THRESHOLD_FRACTION_OF_CRITICAL_SPEED = 0.96

# A session-average intensity above this is not a run, it is a bad stream — a
# GPS jump, a treadmill distance pasted onto a GPS time, a paused recording.
# Generous: a 5 km race averages about 1,06 and a maximal 1500 m about 1,15, so
# nothing an athlete can actually run reaches it over a whole session. Cap
# rather than discard, as the HR rung does: the run happened, we only refuse to
# let one artefact spike ATL.
MAX_RUN_INTENSITY_FACTOR = 1.30

# Metabolic cost of running on a gradient, J/kg/m, as a polynomial in the
# gradient i (rise / run) — Minetti, Moia, Roi, Susta & Ferretti (2002),
# measured on a treadmill from -45 % to +45 %. The grade-adjustment factor is
# C(i)/C(0); C(0) = 3,6 J/kg/m is the flat cost, the constant term below.
GRADE_COST_COEFFS: tuple[float, ...] = (155.4, -30.4, -43.3, 46.3, 19.5, 3.6)
FLAT_COST_J_PER_KG_M = GRADE_COST_COEFFS[-1]

#: Beyond the range Minetti measured the polynomial is extrapolating; clamp.
MAX_ABS_GRADE = 0.45

# The floor on the downhill discount. Minetti measured *metabolic* cost, which
# keeps falling to about 55 % of flat near -20 % gradient — and a run priced
# that way reads as a recovery jog. What the polynomial cannot see is the
# eccentric braking load of descending, which is where the muscle damage of a
# hilly run actually comes from (and what #717 has to account for). So the
# discount stops at 20 %: gravity helps, but not as much as oxygen uptake alone
# suggests.
MIN_GRADE_COST_FACTOR = 0.80

# Grade is computed over segments of at least this many metres. Raw GPS altitude
# wobbles by a metre between consecutive samples, and a metre over three metres
# is a 33 % gradient — which, left unsmoothed, turns a flat road into alternating
# cliffs and makes grade adjustment add noise instead of removing it.
GAP_SEGMENT_METRES = 30.0

# Running zones as fractions of threshold *speed*, with the six names they are
# usually given. Five edges, six zones, same shape as
# ``analysis._POWER_ZONE_BOUNDARY_FRACTIONS``.
PACE_ZONE_BOUNDARY_FRACTIONS: tuple[float, ...] = (0.78, 0.88, 0.95, 1.05, 1.15)
PACE_ZONE_NAMES: tuple[str, ...] = (
    "Recovery",
    "Endurance",
    "Tempo",
    "Threshold",
    "VO2max",
    "Anaerobic",
)

# How confident the Critical Speed fit has to be before a threshold pace derived
# from it is allowed to price sessions. Below it the run still gets a load — from
# heart rate, or from its duration — which is the honest answer: an rTSS computed
# against a threshold pace we are guessing at is not a better number than an
# hrTSS, it only looks like one.
MIN_CS_CONFIDENCE_FOR_LOAD = 0.5

#: Marker stored on a run's ``perf_signals`` blob. See :func:`is_run_signals`.
RUN_SIGNALS_SPORT = "running"


def _positive(value: object) -> float | None:
    """A strictly positive float, or ``None`` for anything else.

    Shaped like ``training_load._positive`` and ``strength_model._positive``:
    all three read numbers that arrive from a provider payload or a form as
    strings, ``None`` or nonsense, and all three have to answer "is there a
    usable number here" rather than raise.
    """
    if isinstance(value, bool):
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number if number > 0 else None


# ---------------------------------------------------------------------------
# Pace and speed, which are the same quantity written upside down
# ---------------------------------------------------------------------------


def pace_seconds_per_km(speed_m_s: float | int | None) -> float | None:
    """Seconds per kilometre for a speed in m/s. ``None`` for a non-speed.

    Kept in one place because the inversion is where pace code goes wrong:
    faster is a *smaller* pace, so every comparison, bound and zone edge flips
    sign on the way through.
    """
    speed = _positive(speed_m_s)
    if speed is None:
        return None
    return 1000.0 / speed


def speed_from_pace_seconds(pace_seconds_per_km: float | int | None) -> float | None:
    """m/s for a pace in seconds per kilometre. ``None`` for a non-pace."""
    pace = _positive(pace_seconds_per_km)
    if pace is None:
        return None
    return 1000.0 / pace


def format_pace(speed_m_s: float | int | None) -> str | None:
    """``"4:12 /km"`` — the only form a pace should ever be shown in.

    A pace printed as a decimal number of minutes ("4,2 /km") reads as 4:12 to
    nobody, and a bare "252" is not a pace at all.
    """
    pace = pace_seconds_per_km(speed_m_s)
    if pace is None:
        return None
    total = int(round(pace))
    return f"{total // 60}:{total % 60:02d} /km"


# ---------------------------------------------------------------------------
# Grade-adjusted pace
# ---------------------------------------------------------------------------


def grade_cost_factor(grade: float | int | None) -> float:
    """Cost of running at ``grade`` relative to flat (Minetti et al. 2002).

    ``grade`` is rise over run, so 0,05 is a 5 % climb and -0,05 a 5 % descent.
    Returns 1,0 for flat ground and for an unreadable grade — the neutral
    answer, because a missing gradient must leave the pace alone rather than
    adjust it in some direction.

    Clamped twice, for two different reasons stated at
    :data:`MAX_ABS_GRADE` and :data:`MIN_GRADE_COST_FACTOR`.
    """
    if isinstance(grade, bool) or not isinstance(grade, (int, float)):
        return 1.0
    value = float(grade)
    if value != value:
        return 1.0
    value = max(-MAX_ABS_GRADE, min(MAX_ABS_GRADE, value))
    cost = 0.0
    for coeff in GRADE_COST_COEFFS:
        cost = cost * value + coeff
    return max(MIN_GRADE_COST_FACTOR, cost / FLAT_COST_J_PER_KG_M)


def grade_adjusted_speed(
    speed_m_s: float | int | None, grade: float | int | None
) -> float | None:
    """The flat speed that would have cost what ``speed_m_s`` at ``grade`` did.

    Multiplication, not division: climbing is dearer per metre, so the
    equivalent flat speed is *faster* than the speed actually run. Getting this
    backwards makes every hill run look easy, which is the failure mode grade
    adjustment exists to fix.
    """
    speed = _positive(speed_m_s)
    if speed is None:
        return None
    return speed * grade_cost_factor(grade)


# ---------------------------------------------------------------------------
# Streams in, envelope out
# ---------------------------------------------------------------------------


def _numeric_stream(streams: object, key: str) -> list[float]:
    if not isinstance(streams, dict):
        return []
    stream = streams.get(key)
    if not isinstance(stream, dict):
        return []
    data = stream.get("data")
    if not isinstance(data, list):
        return []
    return [
        float(value)
        for value in data
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]


def cumulative_distance(streams: dict) -> list[float] | None:
    """Metres travelled at each sample, or ``None`` when the run has no distance.

    The provider's own ``distance`` stream when there is one, because it is the
    measurement; otherwise the integral of ``velocity_smooth`` over ``time``,
    because Strava does not return distance unless asked and older imports were
    not. The integral is the weaker of the two — it inherits whatever smoothing
    the provider applied to the speed — which is why it is the fallback and not
    the rule (#466).

    Monotonic by construction: a negative step is a GPS artefact, not the
    athlete running backwards, so it contributes nothing rather than subtracting.
    """
    time_data = _numeric_stream(streams, "time")
    if len(time_data) < 2:
        return None

    distance = _numeric_stream(streams, "distance")
    if len(distance) == len(time_data):
        cumulative = [0.0]
        total = 0.0
        for previous, current in zip(distance, distance[1:]):
            total += max(0.0, current - previous)
            cumulative.append(total)
        return cumulative

    speeds = _numeric_stream(streams, "velocity_smooth")
    if len(speeds) != len(time_data):
        return None
    cumulative = [0.0]
    total = 0.0
    for index in range(1, len(time_data)):
        step_seconds = time_data[index] - time_data[index - 1]
        if step_seconds > 0:
            total += max(0.0, speeds[index]) * step_seconds
        cumulative.append(total)
    return cumulative


def grade_adjusted_distance(
    streams: dict, distance: list[float] | None = None
) -> list[float] | None:
    """Cumulative grade-adjusted metres, or ``None`` when no usable altitude.

    Each segment of at least :data:`GAP_SEGMENT_METRES` is given one gradient
    and one cost factor; the final stretch, when it is shorter than that, keeps
    the previous segment's factor rather than being dropped or given a gradient
    of its own — so the total distance is preserved without inventing a cliff
    out of the last few metres. ``None`` rather than a copy of the raw distance
    when altitude is missing or misaligned, so the caller can say which figure
    it is holding instead of claiming an adjustment it did not make.
    """
    distances = distance if distance is not None else cumulative_distance(streams)
    if distances is None:
        return None
    altitude = _numeric_stream(streams, "altitude")
    if len(altitude) != len(distances):
        return None

    adjusted = [0.0] * len(distances)
    total = 0.0
    segment_start = 0
    last = len(distances) - 1
    last_factor = 1.0
    for index in range(1, len(distances)):
        run = distances[index] - distances[segment_start]
        if run < GAP_SEGMENT_METRES and index < last:
            continue
        if run >= GAP_SEGMENT_METRES:
            rise = altitude[index] - altitude[segment_start]
            last_factor = grade_cost_factor(rise / run) if run > 0 else 1.0
        # Else: this is the tail of the run, too short to carry a gradient of its
        # own, so it keeps the previous segment's. Giving a four-metre remainder
        # its own gradient is the noise this function exists to remove.
        for position in range(segment_start + 1, index + 1):
            step = max(0.0, distances[position] - distances[position - 1])
            total += step * last_factor
            adjusted[position] = total
        segment_start = index
    return adjusted


def best_mean_speed(
    distance: list[float], time_stream: list[float], n_minutes: float
) -> tuple[float | None, int, int]:
    """Best mean speed over a contiguous ``n_minutes`` window, m/s.

    Returns ``(speed, start_index, end_index)``, both indices inclusive, and
    ``(None, 0, 0)`` when the run is too short to contain the window.

    Exact where the power analogue cannot be: ``analysis.best_n_min_power`` has
    to average the samples inside the window, which assumes even sampling, while
    distance is the integral of speed — so Δd/Δt *is* the mean speed over the
    window however irregularly the device recorded.
    """
    target_secs = n_minutes * 60.0
    n = len(distance)
    if n == 0 or len(time_stream) != n or target_secs <= 0:
        return None, 0, 0

    best_speed = 0.0
    best_start = 0
    best_end = 0
    left = 0
    for right in range(n):
        while time_stream[right] - time_stream[left] > target_secs:
            left += 1
        actual = time_stream[right] - time_stream[left]
        # Same 90 % rule the power envelope uses: a window short of its target
        # would overstate the mean, and a GPS gap at the start of a run is the
        # usual way one arrives.
        if actual >= target_secs * 0.9 and actual > 0:
            speed = (distance[right] - distance[left]) / actual
            if speed > best_speed:
                best_speed = speed
                best_start = left
                best_end = right

    return (best_speed if best_speed > 0 else None), best_start, best_end


def speed_curve(
    distance: list[float],
    time_stream: list[float],
    durations_minutes: tuple[float, ...] = RUN_SIGNAL_DURATIONS_MIN,
) -> dict[str, float]:
    """``{"5": 4.21, ...}`` — best mean speed in m/s per probed duration.

    Keys are stringified whole minutes, matching the ``power_curve`` blob so the
    two envelopes read the same way on the way back out of the database.
    """
    if not distance or len(distance) != len(time_stream):
        return {}
    total_secs = time_stream[-1] - time_stream[0]
    curve: dict[str, float] = {}
    for minutes in durations_minutes:
        # Only probe durations the run is long enough to actually contain.
        if total_secs < minutes * 60 * 0.9:
            continue
        best, _, _ = best_mean_speed(distance, time_stream, minutes)
        if best is not None:
            curve[str(int(minutes))] = round(best, 3)
    return curve


def run_performance_signals(
    streams: dict,
    duration_seconds: int | float | None = None,
) -> dict | None:
    """Per-run signals for ``RideMetric.perf_signals``, or ``None``.

    The running counterpart of ``analysis.compute_ride_performance_signals``,
    stored in the same column and distinguished by a ``sport`` marker rather
    than by the absence of a power curve — see :func:`is_run_signals` for why
    that marker is load-bearing.

    The blob holds the pace–duration envelope (raw and grade-adjusted), the
    distance both ways, the run's duration, the HR response, and the two halves
    of the run so durability can be read from pace decay (#717). It deliberately
    does *not* hold ``first_half_power`` or a ``power_curve`` even when the run
    carried a footpod: those keys are what the cycling inference engine reads,
    and a Stryd run landing in them would argue about a cycling threshold.
    """
    time_data = _numeric_stream(streams, "time")
    distance = cumulative_distance(streams)
    if distance is None or len(time_data) < 2:
        return None

    total_secs = time_data[-1] - time_data[0]
    if total_secs <= 0 or distance[-1] <= 0:
        return None

    adjusted = grade_adjusted_distance(streams, distance)
    signals: dict = {
        "sport": RUN_SIGNALS_SPORT,
        "duration_s": round(total_secs),
        "distance_m": round(distance[-1]),
        "speed_curve": speed_curve(distance, time_data),
    }
    if duration_seconds is not None:
        moving = _positive(duration_seconds)
        if moving is not None:
            signals["moving_duration_s"] = round(moving)
    if adjusted is not None:
        signals["gap_distance_m"] = round(adjusted[-1])
        signals["gap_speed_curve"] = speed_curve(adjusted, time_data)

    hr_data = _numeric_stream(streams, "heartrate")
    usable_hr = hr_data if len(hr_data) == len(time_data) else None
    if usable_hr:
        signals["avg_hr"] = round(sum(usable_hr) / len(usable_hr))
        signals["max_hr"] = round(max(usable_hr))

    mid_time = time_data[0] + total_secs / 2.0
    split = next(
        (i for i, t in enumerate(time_data) if t >= mid_time), len(time_data) // 2
    )
    if 0 < split < len(time_data) - 1:
        first_secs = time_data[split] - time_data[0]
        second_secs = time_data[-1] - time_data[split]
        if first_secs > 0 and second_secs > 0:
            signals["first_half_speed"] = round(
                (distance[split] - distance[0]) / first_secs, 3
            )
            signals["second_half_speed"] = round(
                (distance[-1] - distance[split]) / second_secs, 3
            )
        if usable_hr:
            first_hr = usable_hr[:split]
            second_hr = usable_hr[split:]
            if first_hr and second_hr:
                signals["first_half_hr"] = round(sum(first_hr) / len(first_hr))
                signals["second_half_hr"] = round(sum(second_hr) / len(second_hr))

    return signals


def is_run_signals(signals: object) -> bool:
    """Whether a ``perf_signals`` blob describes a run rather than a ride.

    One column holds both, so every reader has to ask. The cycling inference
    engine (#476) reads ``power_curve``, ``first_half_power`` and ``duration_s``
    out of this blob to estimate FTP, MAP and durability; a run that reached
    those paths would contribute a run's numbers to a cycling threshold, which
    is the #711 error one level further in.
    """
    return (
        isinstance(signals, dict)
        and signals.get("sport") == RUN_SIGNALS_SPORT
    )


# ---------------------------------------------------------------------------
# Critical Speed and D′
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CriticalSpeedFit:
    """A Critical Speed fit, with enough of its own provenance to be judged.

    ``max_residual`` and ``span_minutes`` are carried rather than reduced to a
    single confidence number here, because confidence also depends on *when* the
    efforts were run — which this module cannot see and
    :func:`critical_speed_confidence` is given.
    """

    speed_m_s: float
    d_prime_m: float
    points_used: int
    span_minutes: float
    max_residual: float

    @property
    def threshold_speed_m_s(self) -> float:
        """Functional threshold pace as a speed. See the fraction's rationale."""
        return self.speed_m_s * THRESHOLD_FRACTION_OF_CRITICAL_SPEED

    @property
    def threshold_pace_seconds_per_km(self) -> float:
        return 1000.0 / self.threshold_speed_m_s

    @property
    def critical_pace_seconds_per_km(self) -> float:
        return 1000.0 / self.speed_m_s


def critical_speed_from_points(points: dict[float, float]) -> CriticalSpeedFit | None:
    """Fit CS and D′ from a maximal speed–duration envelope, or refuse.

    ``points`` maps a duration in minutes to the best mean speed held for it in
    m/s. The model is ``distance = CS × t + D′`` (Monod & Scherrer's linear
    form of the hyperbola), fitted by least squares on distance against time.

    ``None`` whenever the envelope cannot support the fit — too few points, too
    narrow a span, a curve that does not descend, a residual the hyperbola
    cannot explain, or a CS or D′ outside what a runner can be. Refusing is the
    point: a Critical Speed invented from three easy runs would set the
    athlete's threshold pace, their zones and every subsequent rTSS figure, and
    it would look exactly like a measured one.
    """
    usable = {
        minutes: speed
        for minutes, speed in (points or {}).items()
        if minutes in CRITICAL_SPEED_DURATIONS and (_positive(speed) is not None)
    }
    if len(usable) < MIN_CS_POINTS:
        return None

    durations = sorted(usable)
    span = durations[-1] - durations[0]
    if span < MIN_CS_SPAN_MINUTES:
        return None

    shortest = usable[durations[0]]
    longest = usable[durations[-1]]
    if shortest < longest * MIN_CS_CURVE_DECLINE:
        return None

    times = [minutes * 60.0 for minutes in durations]
    distances = [usable[minutes] * minutes * 60.0 for minutes in durations]
    mean_t = sum(times) / len(times)
    mean_d = sum(distances) / len(distances)
    denominator = sum((t - mean_t) ** 2 for t in times)
    if denominator <= 0:
        return None
    cs = (
        sum((times[i] - mean_t) * (distances[i] - mean_d) for i in range(len(times)))
        / denominator
    )
    if cs <= 0:
        return None
    d_prime = mean_d - cs * mean_t
    if not (D_PRIME_MIN_M <= d_prime <= D_PRIME_MAX_M):
        return None
    if not (longest * CS_LOWER_BOUND_OF_LONGEST <= cs < longest):
        return None

    # Does the hyperbola the fit implies actually pass through the points?
    residual = 0.0
    for minutes in durations:
        predicted = cs + d_prime / (minutes * 60.0)
        residual = max(residual, abs(predicted - usable[minutes]) / usable[minutes])
    if residual > MAX_CS_SPEED_RESIDUAL:
        return None

    return CriticalSpeedFit(
        speed_m_s=round(cs, 3),
        d_prime_m=round(d_prime, 1),
        points_used=len(usable),
        span_minutes=round(span, 1),
        max_residual=round(residual, 4),
    )


def critical_speed_confidence(
    fit: CriticalSpeedFit, recent_days: int | None = None
) -> float:
    """How much to trust a fit, on the engine's 0-1 scale.

    Four things move it, and all four are about the *evidence* rather than the
    arithmetic: how many points the envelope offered, how wide they spread, how
    well the hyperbola fits them, and how long ago they were run.

    The ceiling is deliberately below 1: every point here is a best effort
    *observed* in training, and we never know an athlete was trying. Only a
    dedicated time-trial protocol would earn more, and this module cannot tell
    that it happened.
    """
    confidence = 0.35
    confidence += min(fit.points_used - MIN_CS_POINTS, 3) * 0.07
    if fit.span_minutes >= 2 * MIN_CS_SPAN_MINUTES:
        confidence += 0.08
    confidence += (1.0 - fit.max_residual / MAX_CS_SPEED_RESIDUAL) * 0.15
    if recent_days is not None:
        if recent_days <= 21:
            confidence += 0.10
        elif recent_days > 60:
            confidence -= 0.15
    return round(max(0.05, min(0.8, confidence)), 2)


# ---------------------------------------------------------------------------
# What the athlete's threshold pace is worth: zones and load
# ---------------------------------------------------------------------------


def pace_zone_boundaries(threshold_speed_m_s: float | int | None) -> list[dict]:
    """The six running zones as pace ranges for a threshold speed.

    Each entry: ``{"zone": "Z2", "name": "Endurance", "fast_pace_seconds_per_km": 300,
    "slow_pace_seconds_per_km": 338}``. ``slow_pace_seconds_per_km`` is ``None`` for Z1
    (open below) and ``fast_pace_seconds_per_km`` is ``None`` for the top zone (open
    above). ``[]`` for a non-positive threshold.

    Named fast/slow rather than low/high because pace inverts speed: the zone
    with the *higher* speed bound has the *lower* pace number, and a pair of
    keys called low/high would be read the wrong way round by every caller
    sooner or later.
    """
    threshold = _positive(threshold_speed_m_s)
    if threshold is None:
        return []
    zones: list[dict] = []
    for index, name in enumerate(PACE_ZONE_NAMES):
        lower_fraction = (
            PACE_ZONE_BOUNDARY_FRACTIONS[index - 1] if index > 0 else None
        )
        upper_fraction = (
            PACE_ZONE_BOUNDARY_FRACTIONS[index]
            if index < len(PACE_ZONE_BOUNDARY_FRACTIONS)
            else None
        )
        # The faster edge of the zone is its *upper* speed fraction.
        fast = (
            round(1000.0 / (threshold * upper_fraction))
            if upper_fraction is not None
            else None
        )
        slow = (
            round(1000.0 / (threshold * lower_fraction))
            if lower_fraction is not None
            else None
        )
        zones.append(
            {
                "zone": f"Z{index + 1}",
                "name": name,
                "fast_pace_seconds_per_km": fast,
                "slow_pace_seconds_per_km": slow,
            }
        )
    return zones


def run_intensity_factor(
    *,
    gap_speed_m_s: float | int | None,
    threshold_speed_m_s: float | int | None,
) -> float | None:
    """Grade-adjusted speed as a fraction of threshold speed, capped.

    ``None`` when either speed is missing — the caller then has no pace model
    for this athlete and must fall to the heart-rate rung rather than assume one.
    """
    speed = _positive(gap_speed_m_s)
    threshold = _positive(threshold_speed_m_s)
    if speed is None or threshold is None:
        return None
    return min(speed / threshold, MAX_RUN_INTENSITY_FACTOR)


def run_training_load(
    *,
    duration_seconds: float | int | None,
    gap_speed_m_s: float | int | None,
    threshold_speed_m_s: float | int | None,
) -> float | None:
    """rTSS: an hour at threshold pace is 100, quadratic below it.

    The same form as cycling TSS and hrTSS, which is what lets the three share a
    CTL — ``hours × IF² × 100``, with IF taken on grade-adjusted speed against
    the athlete's threshold pace. This is the figure TrainingPeaks publishes as
    rTSS (there written ``duration × NGP × IF / (FTPa × 3600) × 100``, which is
    the same expression once NGP/FTPa is substituted for IF).

    ``None`` when the duration or either speed is missing, so the ladder in
    ``services.training_load`` falls through to a rung that can answer.
    """
    duration = _positive(duration_seconds)
    intensity = run_intensity_factor(
        gap_speed_m_s=gap_speed_m_s, threshold_speed_m_s=threshold_speed_m_s
    )
    if duration is None or intensity is None:
        return None
    return round(duration / 3600.0 * intensity * intensity * 100.0, 1)


def mean_gap_speed(signals: object) -> float | None:
    """Mean grade-adjusted speed of a run, from its stored signals.

    Prefers the grade-adjusted distance and falls back to the raw one, because a
    flat run's GAP distance *is* its distance and refusing to price a run with
    no altitude stream would drop every treadmill session to the HR rung.

    Uses the run's own recorded duration, not its elapsed time: a 10-minute
    coffee stop inside a long run would otherwise halve its mean pace and with
    it the whole session's load.
    """
    if not isinstance(signals, dict):
        return None
    duration = _positive(signals.get("moving_duration_s")) or _positive(
        signals.get("duration_s")
    )
    distance = _positive(signals.get("gap_distance_m")) or _positive(
        signals.get("distance_m")
    )
    if duration is None or distance is None:
        return None
    return distance / duration
