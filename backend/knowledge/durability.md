# Durability and Fatigue Resistance

## Overview

Durability is the ability to hold physiological function late in a long ride —
after several hours and several thousand kilojoules of work. It is measured as
the *decline* in a performance marker after prolonged exercise, not as a fresh
capacity, and it is largely independent of the numbers a rider produces rested
(Maunder et al., 2021).

This is why two riders with the same FTP can finish a five-hour ride very
differently. Laboratory profiling has traditionally measured VO2max, threshold
and efficiency in a rested athlete, which describes the first hour well and the
fifth hour poorly. Most cycling races, and almost every gran fondo, are decided
in the fifth hour.

## What "durability" actually measures

The standard approach is a **before-and-after comparison**: establish a marker
fresh, impose prolonged work, then re-measure the same marker.

| Marker | Typical protocol | Durable rider | Poor durability |
|--------|-----------------|---------------|-----------------|
| Critical power / FTP | Re-test after 2–3 h or a fixed kJ load | −0 to −5% | −10 to −20% |
| Power at a fixed lactate | Same lactate step test, fresh vs. fatigued | Small rightward shift | Large leftward shift |
| Power at a fixed heart rate | Steady effort, first vs. last hour | Stable | Falls noticeably |
| Gross efficiency | Same absolute power, fresh vs. fatigued | Stable | Declines |

The load matters more than the clock. Durability decline scales with
**accumulated work (kJ)** and with the intensity of that work, so three hours of
racing degrade performance far more than three hours of Zone 2. Recent work in
amateur road cyclists found the size of the late-ride power decline predicted
competitive success independently of fresh power (Barsumyan et al., 2025).

## Cardiac drift and decoupling

**Cardiovascular drift** is the gradual rise in heart rate, and fall in stroke
volume, during prolonged steady exercise at constant power. It is driven by
rising core temperature and progressive loss of plasma volume through sweating,
and it is not by itself a sign of poor fitness (Coyle & González-Alonso, 2001).

**Aerobic decoupling** turns this into a usable field metric. Split a steady ride
in half and compare the power-to-heart-rate ratio of each half:

```
decoupling (%) = ((P/HR)_first_half − (P/HR)_second_half) / (P/HR)_first_half × 100
```

| Decoupling | Reading |
|-----------|---------|
| < 5% | Aerobically durable at that intensity and duration |
| 5–10% | Approaching the limit of current durability |
| > 10% | Beyond it — or under-fuelled, dehydrated, or overheated |

Interpretation caveats that matter more than the number:

- Only valid for **steady** efforts. Interval sessions and variable terrain make
  the ratio meaningless.
- Heat, dehydration and inadequate fuelling all inflate decoupling without any
  change in fitness. A single high reading is a question, not a verdict.
- It is intensity- and duration-specific. Low decoupling in a two-hour Zone 2
  ride says nothing about the fifth hour of a race.

## The physiology underneath

Three mechanisms account for most of the late-ride decline, and they call for
different responses:

1. **Glycogen depletion.** Muscle glycogen is finite. As stores fall, the same
   external power requires a greater fraction of remaining capacity, and
   high-intensity efforts become disproportionately harder. In elite road
   cyclists, substrate use shifts measurably over a prolonged intermittent ride,
   and glycogen availability is closely tied to the capacity to keep producing
   power late (Ørtenblad et al., 2024).

2. **Thermoregulatory and fluid cost.** Rising core temperature and falling
   plasma volume raise cardiovascular strain at unchanged power — the drift
   above. This is fixable with fuelling, fluid, cooling and heat adaptation, and
   it is the mechanism most often mistaken for a fitness limitation.

3. **Neuromuscular and metabolic fatigue.** Accumulated work reduces the muscle's
   capacity to produce force efficiently. This is the component that responds to
   training rather than to logistics.

The practical consequence: **before treating a late fade as a fitness limiter,
rule out fuelling, hydration and heat.** They are cheaper to fix and are the more
common cause. A rider who fades on 30 g of carbohydrate an hour does not have a
durability problem; they have a fuelling problem.

## Training durability

Durability responds to specific work, not simply to more volume:

- **Long rides that reach the fatigued state.** The adaptation happens in the
  hours the athlete is rarely in. Rides of 3–5 h build the substrate the shorter
  sessions cannot.

- **Intensity late in a long ride.** Placing efforts *after* accumulated work —
  threshold or VO2max intervals in the last hour of a long ride, rather than at
  the start — trains the specific capacity to perform when depleted. This is a
  demanding session and belongs in a build phase, not a base phase.

- **Occasional low-carbohydrate long rides.** Riding some Zone 2 sessions with
  reduced carbohydrate availability increases fat-oxidation capacity and
  glycogen-sparing. Used sparingly: it compromises the quality of hard sessions
  and, done often, impairs recovery, immune function and bone health.

- **Fuelling practice at race intensity.** Gut tolerance for 60–90+ g of
  carbohydrate per hour is itself trainable and must be rehearsed in training,
  not discovered on race day. See `nutrition_timing.md`.

- **Heat adaptation** where the target event is hot, which reduces the
  cardiovascular drift component directly. See `heat_altitude_adaptation.md`.

## Reading it in an athlete's data

Signals that suggest a genuine durability limitation rather than a bad day:

- Power in the final hour of long rides consistently below the first hour at the
  same perceived effort, across several rides.
- Decoupling above 10% on steady rides that were adequately fuelled and not hot.
- A large drop in repeat-effort power late in rides compared with the same
  efforts fresh.
- Normal or strong fresh numbers — a good 20-minute power — alongside poor
  long-ride outcomes. This combination is the signature of a durability limiter
  rather than a threshold or VO2max one.

Counter-evidence worth weighing before naming durability as the limiter: a
single hot day, a ride with under 40 g of carbohydrate per hour, illness, or a
ride following a hard block. Durability is a pattern, not an incident.

## References

- Maunder E, Seiler S, Mildenhall MJ, Kilding AE, Plews DJ (2021). The Importance
  of 'Durability' in the Physiological Profiling of Endurance Athletes.
  *Sports Medicine*, 51(8), 1619–1628. doi:10.1007/s40279-021-01459-0
- Coyle EF, González-Alonso J (2001). Cardiovascular drift during prolonged
  exercise: new perspectives. *Exercise and Sport Sciences Reviews*, 29(2),
  88–92. doi:10.1097/00003677-200104000-00009
- Ørtenblad N, Zachariassen M, Nielsen J, Gejl KD (2024). Substrate utilization
  and durability during prolonged intermittent exercise in elite road cyclists.
  *European Journal of Applied Physiology*, 124(7), 2193–2205.
  doi:10.1007/s00421-024-05437-y
- Barsumyan A, Soost C, Shyla R, Graw JA, Bliemel C, Burchard R (2025).
  Durability as an independent parameter of endurance performance in cycling.
  *BMC Sports Science, Medicine & Rehabilitation*, 17(1), 192.
  doi:10.1186/s13102-025-01238-8
