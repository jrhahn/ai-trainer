# Raising the Threshold

## Overview

Threshold is the intensity boundary above which metabolic demand can no longer be
met in a steady state. Below it, blood lactate stabilises and effort is
sustainable for a long time; above it, lactate rises continuously and time to
exhaustion collapses. Raising that boundary is the single most transferable
adaptation in endurance cycling, because almost every sustained effort — a climb,
a time trial, a breakaway — is decided by how much power sits under it.

`power_zones.md` covers the zone model, `critical_power.md` the power-duration
mathematics, `sweet_spot_training.md` one specific dose. This file is about the
boundary itself: what it is, when it is the limiter, and how to move it.

## The threshold is not one number

Several thresholds are measured and casually equated. They are not the same
thing, and mixing them is the most common source of confused prescriptions
(Faude et al., 2009).

| Concept | What it is | Typical relationship |
|---|---|---|
| **LT1** (aerobic threshold) | First rise in lactate above baseline | ~55–75% of FTP; the top of Zone 2 |
| **MLSS** | Highest constant power at which lactate still stabilises | The physiological reference standard |
| **Critical power (CP)** | Asymptote of the power-duration curve | ~103–108% of FTP; close to but not identical with MLSS |
| **FTP** | Highest power sustainable ~60 min, usually estimated from a 20-min test | The practical field proxy |

MLSS is determined by repeated constant-load trials with blood sampling — the
methodological reference, and impractical outside a lab (Beneke, 2003). CP is
close to it and derivable from field data, though the two are not
interchangeable and the choice of "gold standard" is itself contested
(Jones et al., 2019).

**For training purposes, FTP is a good enough anchor.** Precision matters far
less than consistency: an FTP that is 5% optimistic but stable still produces
sensible zones, while an FTP re-estimated from every good ride produces nonsense.

## When threshold is the limiter

Threshold is not always the right target. The question is where a rider's
sustainable power sits *relative to their aerobic ceiling* — their **fractional
utilization**, FTP as a percentage of maximal aerobic power (MAP) or VO2max.

| Fractional utilization | Reading | Higher-return target |
|---|---|---|
| Below ~72% | Ceiling is well ahead of sustainable power; there is headroom | **Threshold** |
| ~72–80% | Balanced; either can pay | Depends on event and history |
| Above ~80% | Threshold already close to the ceiling | **VO2max** — raise the ceiling |

The principle is old: among well-trained cyclists with similar VO2max, endurance
performance tracks the fraction of that maximum which can be sustained, not the
maximum itself (Coyle et al., 1988). A rider whose FTP is 65% of MAP has a large
engine they are not using; more VO2max work will not help them. A rider at 84%
has extracted nearly everything available and needs a bigger ceiling.

This is why "just do more threshold work" is bad advice in the general case, and
good advice in a specific one.

## Training that raises threshold

Effective threshold development is mostly a question of **accumulated time near
the boundary**, and the ways to accumulate it differ in cost:

**Sweet spot (88–94% FTP).** The highest-yield ratio of adaptive stimulus to
recovery cost. Long intervals (3 × 15–20 min) accumulate substantial time with
modest fatigue, which is why it dominates time-limited training. Detail in
`sweet_spot_training.md`.

**Threshold intervals (95–105% FTP).** More specific and more costly.
2–4 × 10–20 min with 5 min recovery. The classic prescription for a rider who
already has an aerobic base and needs the boundary itself pushed. Two such
sessions a week is a lot; three is usually too many.

**Over-unders (alternating ~95% and ~105% FTP).** Blocks alternating 2 min above
and 2 min below threshold train lactate clearance while continuing to produce it
— specific to the variable demands of racing, where the boundary is repeatedly
crossed rather than held.

**Zone 2 volume.** Often underrated in this context. LT1 and the fat-oxidation
capacity underneath it set the platform on which threshold work is built, and
they respond to duration rather than intensity. A threshold block built on a thin
aerobic base produces a short-lived gain and a tired athlete.

## Practical structure

- **Progress by time-at-intensity, not by power.** Adding a fourth 10-minute
  interval is a more reliable progression than adding 10 W to three of them.
- **Two quality sessions a week** is enough for most riders. The adaptation is
  driven by the total accumulated near-threshold time across the week, and
  recovery is what allows the following week to be as good.
- **Re-test infrequently.** Every 6–8 weeks, or after a block. FTP moves slowly;
  measuring it often mostly measures the day.
- **A block of 4–6 weeks** is typically needed to move the boundary measurably.
  Expect a few percent, not a transformation.

## Common misreadings

- **A good 20-minute power after a taper is not a threshold gain.** Freshness
  raises test numbers without moving the boundary. Compare like with like.
- **Riding "threshold" at 110% FTP** is VO2max work with a threshold label. It
  produces a different adaptation and a much larger recovery cost.
- **Chasing FTP when fractional utilization is already high** spends a block for
  little return. See the table above.
- **A falling FTP is not always lost fitness.** It is often accumulated fatigue,
  and the response is recovery, not more threshold work. See `recovery.md`.

## References

- Faude O, Kindermann W, Meyer T (2009). Lactate threshold concepts: how valid
  are they? *Sports Medicine*, 39(6), 469–490.
  doi:10.2165/00007256-200939060-00003
- Beneke R (2003). Methodological aspects of maximal lactate steady state —
  implications for performance testing. *European Journal of Applied Physiology*,
  89(1), 95–99. doi:10.1007/s00421-002-0783-1
- Jones AM, Burnley M, Black MI, Poole DC, Vanhatalo A (2019). The maximal
  metabolic steady state: redefining the 'gold standard'. *Physiological
  Reports*, 7(10), e14098. doi:10.14814/phy2.14098
- Coyle EF, Coggan AR, Hopper MK, Walters TJ (1988). Determinants of endurance in
  well-trained cyclists. *Journal of Applied Physiology*, 64(6), 2622–2630.
  doi:10.1152/jappl.1988.64.6.2622
