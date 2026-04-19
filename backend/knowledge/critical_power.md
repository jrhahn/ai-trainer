# Critical Power and the Power-Duration Relationship

## Overview

The critical power (CP) model, originally formulated by Monod and Scherrer (1965),
describes the hyperbolic relationship between exercise intensity and the time a
cyclist can sustain that intensity. It is the most physiologically grounded
framework for predicting cycling performance and planning high-intensity training.

## The Two-Parameter CP Model

The CP model rests on two parameters:

| Parameter | Symbol | Meaning | Typical Value (trained cyclist) |
|-----------|--------|---------|--------------------------------|
| Critical Power | CP | Highest power sustainable for a theoretically infinite duration; the boundary between heavy and severe exercise | 250–350 W (elite), 180–260 W (amateur) |
| Anaerobic Work Capacity | W′ (W-prime) | A fixed energy reserve above CP, expressed in joules | 15–30 kJ |

The governing equation is:

```
t = W′ / (P − CP)
```

where *P* is power output (W) and *t* is time to exhaustion (seconds).
Rearranging for power at a given duration:

```
P = W′/t + CP
```

This produces a hyperbolic power-duration curve that describes maximal effort
from approximately 2 minutes to 60 minutes.

## Physiological Meaning

**Critical Power (CP)** corresponds closely (but not exactly) to the maximal
lactate steady state (MLSS) — the highest intensity at which blood lactate
concentration stabilises. It is slightly higher than FTP (~103–108% FTP) and
represents the upper boundary of sustainable aerobic metabolism.

**W′ (W-prime)** is the finite anaerobic (above-CP) work capacity. It is not
purely anaerobic; it also reflects the depletion of oxygen stores, phosphocreatine,
and the ability to buffer hydrogen ions. During hard efforts above CP, W′ depletes
at a rate proportional to how far power exceeds CP. Below CP, W′ reconstitutes
— but reconstitution is slower than depletion.

## Measuring CP and W′

Three common field-test protocols:

1. **3-minute all-out test (Burnley et al., 2006)**: The end-power of a 3-minute
   all-out effort (mean power of the last 30 s) approximates CP. The total
   work performed above that end-power approximates W′. Single-test, repeatable,
   but demanding.

2. **Multiple time trials**: Perform 2–3 maximal efforts of different durations
   (e.g., 3 min, 8 min, 20 min) on separate days. Plot mean power vs. 1/time;
   the y-intercept is CP and the slope is W′.

3. **Ramp test + 3-min test combination**: A ramp test provides VO2max power;
   paired with a 3-min all-out test it allows single-session CP/W′ estimation.

Modern cycling computers (Garmin, Wahoo) and platforms (TrainingPeaks, WKO5)
estimate CP and W′ from race and training data using Monod's linearisation or
non-linear fitting methods.

## CP vs. FTP

| Metric | Definition | Relationship |
|--------|------------|--------------|
| FTP | Highest average power sustainable for ~60 min | Standard training benchmark |
| CP | Asymptote of hyperbolic power-duration curve | Typically 3–8% above FTP |

FTP is a practical field approximation. CP provides a more precise physiological
boundary and enables W′ calculation. For training prescription, FTP zones remain
the norm; CP/W′ is more useful for race-day pacing and interval design.

## W′ Balance Modelling

During a race or hard ride, software can track W′ depletion and reconstitution
in real time (Skiba et al., 2012):

```
W′bal(t) = W′ − ΔW′depletion(t) + ΔW′reconstitution(t)
```

W′ reconstitutes below CP following a mono-exponential function with a time
constant τ_W′ ≈ 300–400 s (varies by individual and recovery intensity).
Coaches use W′ balance models to design optimal interval sessions and predict
late-race fatigue.

## Training Implications

- **Raising CP**: Sustained work near CP (tempo, sweet-spot, threshold intervals)
  shifts the horizontal asymptote upward — the primary goal of base and threshold
  training phases.

- **Expanding W′**: High-intensity efforts well above CP (VO2max and anaerobic
  intervals, 120–150% FTP) can expand W′ over a training block. W′ is especially
  relevant for criterium, cyclo-cross, and road race success where repeated hard
  efforts are common.

- **Race pacing**: Knowing CP and W′ allows precise pacing for time trials and
  climbs. Exceeding CP draws from W′; once W′ is depleted, power must drop to
  CP or below.

## References

- Monod H, Scherrer J (1965). The work capacity of a synergic muscle group.
  *Ergonomics*, 8(3), 329–338.
- Burnley M, Doust JH, Vanhatalo A (2006). A 3-min all-out test to determine
  peak oxygen uptake and the maximal steady state. *Medicine & Science in Sports
  & Exercise*, 38(11), 1995–2003.
- Skiba PF, Chidnok W, Vanhatalo A, Jones AM (2012). Modelling the expenditure
  and reconstitution of work capacity above critical power. *Medicine & Science
  in Sports & Exercise*, 44(8), 1526–1532.
- Jones AM, Vanhatalo A (2010). The 'Critical Power' concept: applications to
  sports performance with a focus on intermittent high-intensity exercise.
  *Sports Medicine*, 47(Suppl 1), 65–78.
