"""Deterministic physiological limiter detection (#477).

Reads the Athlete Performance Model attributes produced by the inference engine
(:mod:`services.athlete_model_inference`) and returns a **ranked** list of
candidate physiological limiters — the single most valuable coaching output:
*where is the athlete currently limited, and why?*

Each candidate carries ``confidence``, ``evidence`` and ``counter_evidence`` and
is never presented as fact. The core reasoning reproduces what a good human coach
does — "if the engine is already big, raise the floor":

- **threshold** — the aerobic ceiling (MAP/VO₂max) is well ahead of sustainable
  threshold power (low *fractional utilization*). There is headroom to raise FTP
  toward the ceiling, so threshold work is expected to pay off most.
- **vo2max** — threshold already sits close to the aerobic ceiling (high
  fractional utilization); further threshold work has little room, so raising
  MAP/VO₂max is the higher-return target.
- **endurance_durability** — power fades late in long rides / high cardiac drift.
- **anaerobic_capacity** — reserved; our data rarely confirms maximal short
  efforts, so it is only surfaced at low confidence.

Like the inference engine this is rule-based and pure (unit-testable). When the
signals are missing it returns a single low-confidence ``insufficient_data``
entry rather than guessing a limiter.

**Per sport, since #718.** Every rule above reasons in watts: MAP against FTP,
fractional utilisation, a power curve. Run over a multisport athlete unchanged,
the chain would have asserted a *runner's* limiter out of cycling evidence — and
it already did something nearly as bad, telling a run-only athlete that it needed
"FTP, MAP and long-ride data" before it could help them.

So each sport has its own rules, its own attribute keys and its own limiter
identifiers, and a candidate carries the ``sport`` it was decided for. Three
properties follow, and each is a test:

- **A limiter is never asserted for a sport the athlete has no data in.** The
  rules for a sport only run when that sport contributed attributes.
- **Evidence cites only same-sport observations.** Structurally, not by
  convention: a sport's rules are handed its own attribute subset and cannot
  reach the other's keys.
- **A cycling-only history reads exactly as it did before**, down to the
  confidences and the ordering. The only difference is the additive ``sport``
  field.

The running rules are the analogue rather than a copy. Critical Speed stands
where FTP stands and the velocity at VO₂max where MAP stands, so the same "if the
engine is already big, raise the floor" reasoning applies — but the bands differ,
because CS sits near 90 % of vVO₂max where FTP sits near 75 % of MAP, and the
durability evidence differs too: a ride fades in watts and a run fades in pace.

Strength is deliberately **not** a sport here. It is a limiter *input* — gym work
supports durability and muscular endurance — and giving it its own limiter chain
would mean asserting that an athlete's limiter is their squat, which is a claim
about a lift rather than about what is holding back their riding or running.
"""

from __future__ import annotations

from typing import Any

from services.activity_identity import SPORT_CYCLING, SPORT_RUNNING

# Limiter identifiers written into AthletePerformanceModel.likely_limiter.
#
# The running ids are distinct strings rather than "threshold" plus a sport
# field, and that is load-bearing: ``likely_limiter`` is a bare ``String(50)``
# column with nowhere to put the sport, so a stored "threshold" would be
# ambiguous the moment a runner's limiter could land in it. The ``sport`` field
# on each candidate is what the ROI and hypothesis stages gate on; the distinct
# id is what keeps the persisted scalar readable on its own.
LIMITER_THRESHOLD = "threshold"
LIMITER_VO2MAX = "vo2max"
LIMITER_DURABILITY = "endurance_durability"
LIMITER_RUN_CRITICAL_SPEED = "run_critical_speed"
LIMITER_RUN_SPEED_CEILING = "run_speed_ceiling"
LIMITER_RUN_DURABILITY = "run_durability"
LIMITER_INSUFFICIENT = "insufficient_data"

# Fractional utilization = FTP / MAP. Well-developed threshold sits ~72-77% of
# maximal aerobic power; below that the ceiling is under-exploited (threshold
# limiter), well above it the ceiling itself constrains (VO₂max limiter).
FRAC_THRESHOLD_LIMITED = 0.72
FRAC_VO2_CEILING = 0.80

# The running pair: Critical Speed as a fraction of the velocity at VO₂max. A
# well-developed CS sits near 90 % of vVO₂max (Jones & Vanhatalo 2017), which is
# a much higher fraction than FTP/MAP — reusing the cycling numbers would call
# every runner alive ceiling-limited and send them all to the track.
#
# The band is wider than the cycling one because the denominator is weaker: the
# ceiling is a 5-minute training effort we cannot know was maximal, not a
# modelled MAP. A wider band means the ratio has to be clearly off-centre before
# either running limiter fires, which is the right response to a noisier input.
RUN_FRAC_CS_LIMITED = 0.84
RUN_FRAC_SPEED_CEILING = 0.92

# Only promote a candidate to model.likely_limiter above this confidence.
LIKELY_LIMITER_MIN_CONFIDENCE = 0.35

# Ordinal ranking of the qualitative scores the inference engine emits.
_AEROBIC_ORDINAL = {"high": 4, "above_average": 3, "moderate": 2, "developing": 1}
_FATIGUE_ORDINAL = {"high": 4, "above_average": 3, "moderate": 2, "fades": 1}

# Which attribute keys belong to which sport. The rules for a sport are handed
# only its own subset, so "evidence cites only same-sport observations" is a
# property of the wiring rather than a thing each rule has to remember.
CYCLING_ATTRIBUTES = (
    "ftp",
    "map",
    "vo2max",
    "fractional_utilization",
    "aerobic_endurance",
    "fatigue_resistance",
    "anaerobic_capacity",
)
RUNNING_ATTRIBUTES = (
    "critical_speed",
    "d_prime",
    "threshold_pace",
    "velocity_at_vo2max",
    "run_fractional_utilization",
    "run_fatigue_resistance",
)


def _limiter(
    name: str,
    confidence: float,
    evidence: list[str],
    counter_evidence: list[str],
    sport: str | None = SPORT_CYCLING,
) -> dict[str, Any]:
    """Build one ranked-limiter dict in the persisted/schema shape.

    ``sport`` defaults to cycling so the existing cycling rules read unchanged;
    ``None`` is for the one candidate that belongs to no sport — the
    ``insufficient_data`` entry for an athlete with no attributes at all.
    """
    return {
        "limiter": name,
        "sport": sport,
        "confidence": round(max(0.0, min(1.0, confidence)), 2),
        "evidence": evidence,
        "counter_evidence": counter_evidence,
    }


def _min_input_confidence(*attrs: dict | None) -> float:
    """Weakest confidence among the attributes a judgement is built on.

    A limiter can never be more certain than the least certain estimate feeding
    it, so this bounds the candidate's confidence.
    """
    confs = [
        a.get("confidence")
        for a in attrs
        if a and isinstance(a.get("confidence"), (int, float))
    ]
    return min(confs) if confs else 0.0


def _threshold_or_vo2_candidate(
    frac: dict, ftp: dict, mp: dict, vo2: dict, aer: dict, fat: dict
) -> dict | None:
    """Decide between a threshold and a VO₂max-ceiling limiter from the gap
    between the aerobic ceiling (MAP) and sustainable threshold power (FTP)."""
    frac_est = frac.get("estimate")
    ftp_est = ftp.get("estimate")
    map_est = mp.get("estimate")
    if frac_est is None or not ftp_est or not map_est:
        return None

    input_conf = _min_input_confidence(frac, ftp, mp)
    aer_ord = _AEROBIC_ORDINAL.get(aer.get("score"))
    fat_ord = _FATIGUE_ORDINAL.get(fat.get("score"))

    if frac_est < FRAC_THRESHOLD_LIMITED:
        signal = min(1.0, (FRAC_THRESHOLD_LIMITED - frac_est) / 0.12)
        evidence = [
            f"Threshold is only {frac_est:.0%} of maximal aerobic power "
            f"(FTP {ftp_est:g} W vs MAP {map_est:g} W); a well-developed threshold "
            "sits near 72-77% of MAP, so there is headroom to raise FTP toward "
            "the aerobic ceiling."
        ]
        counter: list[str] = []
        conf = input_conf * (0.55 + 0.45 * signal)
        if aer_ord is not None and aer_ord >= 3:
            evidence.append(
                f"Aerobic endurance looks {aer.get('score', '').replace('_', ' ')}, "
                "indicating a strong base to build threshold on."
            )
            conf += 0.10
        elif aer_ord is None:
            counter.append(
                "Aerobic base not yet confirmed (no long rides with usable HR), "
                "so the strong-engine assumption is unverified."
            )
            conf -= 0.05
        if fat_ord is not None and fat_ord <= 1:
            counter.append(
                "Power fades late in long rides, which points at durability "
                "rather than threshold."
            )
        return _limiter(LIMITER_THRESHOLD, conf, evidence, counter)

    if frac_est >= FRAC_VO2_CEILING:
        signal = min(1.0, (frac_est - FRAC_VO2_CEILING) / 0.10)
        evidence = [
            f"Threshold already reaches {frac_est:.0%} of maximal aerobic power "
            f"(FTP {ftp_est:g} W vs MAP {map_est:g} W); with threshold this close "
            "to the ceiling, raising MAP/VO₂max offers more headroom than more "
            "threshold work."
        ]
        counter = []
        conf = input_conf * (0.55 + 0.45 * signal)
        if vo2.get("estimate") is None:
            counter.append(
                "VO₂max itself is unknown (no body weight recorded), so the size "
                "of the aerobic ceiling is uncertain."
            )
            conf -= 0.05
        return _limiter(LIMITER_VO2MAX, conf, evidence, counter)

    return None


def _durability_candidate(aer: dict, fat: dict) -> dict | None:
    """Flag endurance durability when power fades late or long-ride drift is high."""
    aer_ord = _AEROBIC_ORDINAL.get(aer.get("score"))
    fat_ord = _FATIGUE_ORDINAL.get(fat.get("score"))
    weak_fatigue = fat_ord is not None and fat_ord <= 1  # "fades"
    weak_aerobic = aer_ord is not None and aer_ord <= 1  # "developing"
    if not (weak_fatigue or weak_aerobic):
        return None

    evidence: list[str] = []
    if weak_fatigue:
        evidence.append(
            f"Fatigue resistance scores '{fat.get('score')}' — power drops "
            "noticeably in the second half of long rides."
        )
    if weak_aerobic:
        evidence.append(
            f"Aerobic endurance scores '{aer.get('score')}' — high cardiac drift "
            "or limited long-ride durability."
        )
    counter: list[str] = []
    if aer_ord is not None and aer_ord >= 3:
        counter.append(
            "Aerobic endurance is otherwise strong, so the late fade may be "
            "situational (fuelling/heat) rather than a fitness limiter."
        )

    base_conf = _min_input_confidence(fat, aer) or 0.2
    severity = (0 if fat_ord is None else 2 - fat_ord) + (
        0 if aer_ord is None else 2 - aer_ord
    )
    conf = min(0.35 + 0.12 * severity, base_conf + 0.15, 0.7)
    return _limiter(LIMITER_DURABILITY, conf, evidence, counter)


def pace_label(speed: object) -> str:
    """A speed in m/s as a pace, for evidence an athlete has to recognise.

    The model stores speeds because it does arithmetic with them; a runner reads
    4:35 /km and recognises it, and reads 3.64 m/s and does not. Every running
    statement in this chain goes through here, so there is one answer for a speed
    that is missing or malformed rather than three — the ROI and hypothesis
    stages import it from here.

    ``run_model`` is imported lazily: it is a heavier module than the cycling
    path needs, and a cycling-only athlete should not pay for the running
    vocabulary to be loaded.
    """
    from services import run_model

    if not isinstance(speed, (int, float)) or isinstance(speed, bool) or speed <= 0:
        return "unknown pace"
    return run_model.format_pace(float(speed))


def _run_speed_candidate(frac: dict, cs: dict, ceiling: dict) -> dict | None:
    """Decide between a Critical Speed and a vVO₂max-ceiling limiter (#718).

    The running analogue of :func:`_threshold_or_vo2_candidate`, with the same
    shape of reasoning and none of its numbers: CS stands where FTP stands,
    vVO₂max where MAP stands, and the well-developed band sits near 90 % rather
    than near 75 %.
    """
    frac_est = frac.get("estimate")
    cs_est = cs.get("estimate")
    ceiling_est = ceiling.get("estimate")
    if frac_est is None or not cs_est or not ceiling_est:
        return None

    input_conf = _min_input_confidence(frac, cs, ceiling)
    # The ceiling is a training effort nobody verified was maximal, so every
    # candidate built on it carries that caveat rather than leaving the reader to
    # infer it from a confidence number.
    shared_counter = [
        "The aerobic ceiling is read from a 5-minute training effort, which may "
        "not have been maximal — if it was submaximal the real gap is larger."
    ]

    if frac_est < RUN_FRAC_CS_LIMITED:
        signal = min(1.0, (RUN_FRAC_CS_LIMITED - frac_est) / 0.10)
        return _limiter(
            LIMITER_RUN_CRITICAL_SPEED,
            input_conf * (0.55 + 0.45 * signal),
            [
                f"Critical Speed is only {frac_est:.0%} of the running aerobic "
                f"ceiling ({pace_label(cs_est)} against {pace_label(ceiling_est)}); a "
                "well-developed Critical Speed sits near 90 % of it, so there is "
                "room to raise CS rather than the ceiling."
            ],
            shared_counter,
            sport=SPORT_RUNNING,
        )

    if frac_est >= RUN_FRAC_SPEED_CEILING:
        signal = min(1.0, (frac_est - RUN_FRAC_SPEED_CEILING) / 0.06)
        return _limiter(
            LIMITER_RUN_SPEED_CEILING,
            input_conf * (0.55 + 0.45 * signal),
            [
                f"Critical Speed already reaches {frac_est:.0%} of the running "
                f"aerobic ceiling ({pace_label(cs_est)} against {pace_label(ceiling_est)}); "
                "with CS this close to the ceiling, raising vVO₂max offers more "
                "headroom than more threshold-pace work."
            ],
            shared_counter,
            sport=SPORT_RUNNING,
        )

    return None


def _run_durability_candidate(fat: dict) -> dict | None:
    """Flag running durability when pace fades through a long run (#718).

    Narrower than the cycling durability rule, which also fires on a weak aerobic
    endurance score. There is no running equivalent of that score — the cycling
    one is built from cardiac drift on long rides — so this rule rests on the
    half-split pace alone rather than borrowing a cycling judgement to stand in
    for the half it does not have.
    """
    fat_ord = _FATIGUE_ORDINAL.get(fat.get("score"))
    if fat_ord is None or fat_ord > 1:  # anything but "fades"
        return None
    evidence = list(fat.get("evidence") or []) or [
        f"Run fatigue resistance scores '{fat.get('score')}'"
    ]
    base_conf = _min_input_confidence(fat) or 0.2
    return _limiter(
        LIMITER_RUN_DURABILITY,
        min(base_conf + 0.15, 0.7),
        [
            "Pace drops noticeably through the second half of long runs, which "
            "points at durability rather than at the aerobic ceiling.",
            *evidence,
        ],
        [
            "A late fade can be fuelling, heat or a run started too fast rather "
            "than a durability ceiling.",
            "Running durability is also a function of recent running exposure "
            "(#717), which is a training-history question rather than a "
            "physiological one.",
        ],
        sport=SPORT_RUNNING,
    )


def _has_signal(attributes: dict[str, dict], keys: tuple[str, ...]) -> bool:
    """Whether this sport contributed anything the rules could read.

    An attribute that exists but says ``unknown`` with no estimate is not data —
    it is the inference engine reporting that it could not tell. Treating it as
    presence is how a sport the athlete has never done acquires a limiter.
    """
    for key in keys:
        attr = attributes.get(key)
        if not isinstance(attr, dict):
            continue
        if attr.get("estimate") is not None:
            return True
        score = attr.get("score")
        if isinstance(score, str) and score not in ("", "unknown"):
            return True
    return False


def _cycling_candidates(attributes: dict[str, dict]) -> list[dict]:
    frac = attributes.get("fractional_utilization") or {}
    ftp = attributes.get("ftp") or {}
    mp = attributes.get("map") or {}
    vo2 = attributes.get("vo2max") or {}
    aer = attributes.get("aerobic_endurance") or {}
    fat = attributes.get("fatigue_resistance") or {}

    candidates: list[dict] = []
    primary = _threshold_or_vo2_candidate(frac, ftp, mp, vo2, aer, fat)
    if primary is not None:
        candidates.append(primary)
    durability = _durability_candidate(aer, fat)
    if durability is not None:
        candidates.append(durability)
    return candidates


def _running_candidates(attributes: dict[str, dict]) -> list[dict]:
    frac = attributes.get("run_fractional_utilization") or {}
    cs = attributes.get("critical_speed") or {}
    ceiling = attributes.get("velocity_at_vo2max") or {}
    fat = attributes.get("run_fatigue_resistance") or {}

    candidates: list[dict] = []
    primary = _run_speed_candidate(frac, cs, ceiling)
    if primary is not None:
        candidates.append(primary)
    durability = _run_durability_candidate(fat)
    if durability is not None:
        candidates.append(durability)
    return candidates


# Per-sport rules, as data: the attribute keys that count as this sport having
# been trained, the function that proposes its candidates, and what to say when it
# has data but nothing conclusive. Adding a sport is an entry here — which is the
# point of #718 — rather than another branch inside ``detect_limiters``.
_SPORT_RULES: tuple[tuple[str, tuple[str, ...], Any, str], ...] = (
    (
        SPORT_CYCLING,
        CYCLING_ATTRIBUTES,
        _cycling_candidates,
        # Deliberately the pre-#718 wording, unchanged. "FTP, MAP and long-ride
        # data" already names the sport, so adding the word "cycling" would move
        # a string every cycling-only athlete sees for no gain.
        "Not enough signal across the model's attributes to identify a limiter "
        "(need FTP, MAP and long-ride data).",
    ),
    (
        SPORT_RUNNING,
        RUNNING_ATTRIBUTES,
        _running_candidates,
        "Not enough signal across the model's running attributes to identify a "
        "limiter (need a Critical Speed fit, a 5-minute maximal effort and long "
        "runs).",
    ),
)


def detect_limiters(attributes: dict[str, dict]) -> list[dict]:
    """Return a confidence-ranked list of candidate physiological limiters.

    ``attributes`` is the Athlete Performance Model attribute map (as produced by
    :func:`services.athlete_model_inference.infer_performance_attributes`). Each
    candidate carries the ``sport`` it was decided for.

    A sport's rules run **only when that sport contributed attributes** (see
    :func:`_has_signal`), so a cyclist is never handed a running limiter and a
    runner is never told to go and measure their FTP. A sport with data but
    nothing conclusive gets its own ``insufficient_data`` entry naming what that
    sport is missing; an athlete with nothing at all gets one sportless entry.

    Ranked by confidence across sports, so ``likely_limiter`` keeps meaning "the
    thing we are most sure about". The tie-break is the sport then the limiter id,
    because an ordering that depended on dict iteration would make the persisted
    ranking non-reproducible.
    """
    candidates: list[dict] = []
    insufficient: list[dict] = []
    for sport, keys, propose, missing in _SPORT_RULES:
        if not _has_signal(attributes, keys):
            continue
        found = propose(attributes)
        if found:
            candidates.extend(found)
        else:
            insufficient.append(
                _limiter(LIMITER_INSUFFICIENT, 0.05, [], [missing], sport=sport)
            )

    if not candidates and not insufficient:
        return [
            _limiter(
                LIMITER_INSUFFICIENT,
                0.05,
                [],
                # Names no sport's requirements, because this is the branch that
                # fires when no sport has data — and the old wording told a
                # run-only athlete to go and measure their FTP.
                [
                    "No sport in the performance model carries enough signal to "
                    "identify a limiter yet."
                ],
                sport=None,
            )
        ]

    candidates.sort(
        key=lambda c: (-c["confidence"], c["sport"] or "", c["limiter"])
    )
    # The per-sport "nothing conclusive" entries sort after every real candidate
    # whatever their confidence: they are the absence of a finding, and a
    # ``top_limiter`` that could land on one would be reporting a gap as a result.
    return candidates + insufficient


def limiters_for_sport(limiters: list[dict] | None, sport: str) -> list[dict]:
    """The candidates decided for one sport, in the order they were ranked.

    Pre-#718 rows carry no ``sport`` key at all; those are read as cycling, which
    is what every limiter in them was.
    """
    return [
        c
        for c in limiters or []
        if (c.get("sport") or SPORT_CYCLING) == sport
        and c.get("limiter") != LIMITER_INSUFFICIENT
    ]


def top_limiter(limiters: list[dict]) -> str | None:
    """The single most-probable limiter to store on ``likely_limiter``.

    Returns ``None`` (leaving the field unset) when the ranking is empty, is only
    ``insufficient_data``, or the top candidate is below
    :data:`LIKELY_LIMITER_MIN_CONFIDENCE` — we would rather say nothing than name a
    limiter we do not believe.
    """
    if not limiters:
        return None
    top = limiters[0]
    if (
        top["limiter"] == LIMITER_INSUFFICIENT
        or top["confidence"] < LIKELY_LIMITER_MIN_CONFIDENCE
    ):
        return None
    return top["limiter"]


def top_limiter_for_sport(limiters: list[dict] | None, sport: str) -> str | None:
    """That sport's most-probable limiter, or ``None``.

    The same confidence gate as :func:`top_limiter`, applied within one sport —
    so a multisport athlete gets at most one top limiter per sport they actually
    train, and a sport whose best candidate is weak gets none rather than
    borrowing the other sport's certainty.
    """
    return top_limiter(limiters_for_sport(limiters, sport))


def top_limiter_by_sport(limiters: list[dict] | None) -> dict[str, str]:
    """Every sport's top limiter, keyed by sport.

    Only sports with a candidate above the gate appear. An athlete who trains one
    sport gets one entry, which is why this is also the honest answer for a
    cycling-only history.
    """
    found: dict[str, str] = {}
    for sport, *_ in _SPORT_RULES:
        limiter = top_limiter_for_sport(limiters, sport)
        if limiter is not None:
            found[sport] = limiter
    return found
