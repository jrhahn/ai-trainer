# Athlete Profile Context for Personalised Coaching

When the AI retrieves knowledge from the RAG corpus it must filter, weight, and
adapt that information to the individual athlete. Generic science is only useful
when matched to the right person. The following dimensions should always be
considered before applying retrieved knowledge.

---

## 1. License / Competitive Level

License level sets the performance ceiling and appropriate training demands.
Advice that is correct for a Category 1 racer can be dangerous or demotivating
for a recreational rider.

| Level | Description | Key Implications |
|---|---|---|
| **Recreational / Unregistered** | No racing, rides for fitness or enjoyment | Safety, sustainability, fun first; avoid jargon; keep intensity moderate |
| **Gran Fondo / Sportive** | Event-focused but non-competitive | Volume matters; target events, not results; emphasise completion over speed |
| **Cat 4 / Cat 5 (USA) or 3rd/4th Cat (UK/EU)** | Entry-level licensed racer | Developing skills, tactics, basic periodization; aerobic base priority |
| **Cat 2 / Cat 3** | Intermediate licensed racer | Structured training, race-specific work, lactate threshold focus |
| **Cat 1 / Elite Amateur** | High-commitment, near-pro demands | Polarised training, block periodization, full season planning |
| **Masters (35+, 40+, 45+…)** | Age-graded competition | Recovery priority, reduced frequency, hormonal considerations (GH, testosterone) |
| **Professional / Semi-professional** | Full-time athlete | High volume, advanced periodization, extensive recovery support |

**Always downscale volume and intensity targets when a rider is new to structured
training, regardless of stated license level.**

---

## 2. Sport / Discipline

Different disciplines demand different energy systems, muscle patterns, and
knowledge emphasis. Retrieve and apply sport-specific knowledge where available.

### Cycling Sub-disciplines

| Discipline | Primary Demand | RAG Knowledge to Emphasise |
|---|---|---|
| Road (general) | Aerobic base + varied intensity | Periodization, polarised training, sweet spot, FTP |
| Criterium / Circuit | Anaerobic capacity, repeated sprints | HIIT, sprint training, W-prime reconstitution |
| Time Trial / Triathlon bike | Sustained threshold power, aerodynamics | Critical power, threshold training, pacing |
| Climbing / Hilly sportives | High w/kg, sustained power, VO2max | Power zones, VO2max intervals, nutrition on long efforts |
| Gravel / Ultra-distance | High volume, fat oxidation, durability | Endurance base, fuelling, heat/altitude adaptation |
| Mountain Bike (XC) | Aerobic + sharp bursts, technical | HIIT, bike handling, muscle endurance |
| Track | Power, speed, short-duration systems | Sprint training, neuromuscular power, W-prime |

### Other Endurance Sports

| Sport | Notes |
|---|---|
| Running | HR-based zones (no power), run economy, impact load and injury risk; avoid cycling-specific power advice |
| Triathlon | Multi-sport periodization, brick training, swim/bike/run balance, minimise accumulated fatigue; transition-specific advice |
| Duathlon / Multisport | Similar to triathlon; emphasise run-bike-run specificity |

---

## 3. Training Context

The athlete's current training situation determines which knowledge is immediately
relevant and safe to apply.

### Training Phase

| Phase | Appropriate Focus |
|---|---|
| **Off-season / Transition** | Recovery, technique work, cross-training; very low intensity guidance |
| **Base / General Preparation** | Volume, aerobic efficiency, Zone 2 emphasis, nutrition for long rides |
| **Build / Specific Preparation** | Threshold and sweet-spot blocks, race-specific simulations |
| **Peak / Pre-competition** | Tapering, sharpening, race tactics, fuelling strategy |
| **In-season / Racing** | Recovery between events, race analysis, form maintenance |
| **Post-injury / Return to training** | Gradual load increase, injury-risk monitoring, conservative targets |

### Training Volume and History

- **Training age** (years of structured training) affects how aggressively load
  can increase; a 1-year trained rider follows different rules than a 10-year veteran.
- **Current weekly hours** sets the baseline for volume prescriptions.
- **Recent disruption** (illness, travel, life stress) requires load reduction
  before resuming normal training; never assume continuity.
- **Consistency** over the past 4–8 weeks is more predictive of readiness than
  peak historical fitness.

### Fitness Metrics

| Metric | Use |
|---|---|
| **FTP (watts)** | Set power zone targets for cycling; never estimate from single activities |
| **FTP (w/kg)** | Normalised for climbers and weight-sensitive comparisons |
| **Threshold HR (bpm)** | Primary zone target when power data is unavailable |
| **VO2max (ml/kg/min)** | Indicates aerobic ceiling; informs HIIT and altitude responses |
| **Critical Power (CP) + W′** | Precision pacing for hard efforts; apply W-prime reconstitution rules |
| **HRV baseline (RMSSD)** | Daily readiness; compare to personal 7-day rolling baseline, not population norms |
| **Resting HR trend** | Secondary readiness signal; rising resting HR often precedes overreaching |

---

## 4. Athlete Characteristics

### Age

- **Under 18**: Growth-plate risk; avoid heavy strength training; emphasise skill
  development over physiological loading.
- **18–35**: Standard training prescriptions apply.
- **35–50 (Masters I/II)**: Recovery takes longer; reduce high-intensity session
  frequency; strength training becomes more important to offset power decline.
- **50+ (Masters III+)**: Hormonal decline affects adaptation rate; sleep and
  recovery are the primary performance levers; volume reduces before intensity.

### Body Weight and Composition

- **Power-to-weight ratio (w/kg)** is the key metric for hilly/climbing context.
- Avoid prescribing weight loss without clear indication it is safe and desired.
- Fuelling advice must account for body weight for carbohydrate dose calculations
  (g/kg targets).

### Health Flags

- **Existing injury**: Modify or exclude exercises that load the affected area.
- **Illness (current or recent)**: Do not prescribe intensity increases; wait
  for full recovery before resuming hard training.
- **Cardiac conditions**: Always defer to a physician for intensity limits;
  never override medical advice.
- **Pregnancy / postpartum**: Significant modifications required; defer to
  medical guidance.

---

## 5. Goals and Race Calendar

Retrieved knowledge should always be tied back to the athlete's stated goals.

- **Target event type** determines which energy systems and training methods are
  prioritised.
- **Time to target event** determines which training phase is appropriate and
  how aggressive the training ramp can be.
- **A / B / C race hierarchy**: Only taper and peak for A-priority events;
  treat B events as quality training with abbreviated taper (3–5 days); race
  C events on normal training load.
- **Absence of a race goal**: Long-term aerobic development and health take
  priority; avoid peaking for nothing.

---

## 6. Environmental and Logistical Constraints

- **Available training days per week**: Never schedule more sessions than the
  athlete can realistically complete; an unexecuted plan is useless.
- **Equipment**: Power meter availability changes which knowledge is actionable;
  fall back to HR-based zones if no power data.
- **Climate and geography**: Heat, altitude, and terrain require specific
  adaptations — apply heat/altitude knowledge when relevant.
- **Indoor vs outdoor**: Indoor training is typically more intensity-efficient
  but mentally harder; account for this in session prescriptions.

---

## 7. How to Apply This Context to RAG Knowledge

1. **Retrieve relevant chunks** based on the user's question and sport.
2. **Filter by relevance to level**: discard elite-specific techniques for
   recreational athletes (e.g., do not prescribe 30-hour training weeks to a
   beginner).
3. **Adapt numeric targets** (power, HR, volume) to the individual's baseline
   metrics rather than citing population averages verbatim.
4. **Acknowledge missing data**: if FTP, age, or training history is unknown,
   state the assumption being made and invite the athlete to provide the value.
5. **Prioritise safety over performance**: when in doubt, apply conservative
   recommendations and refer to professional guidance for medical questions.
6. **Do not mix sport knowledge inappropriately**: cycling power-zone advice
   is invalid for run sessions; always check the active sport context before
   applying retrieved knowledge.
