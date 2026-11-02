"""Pain that lasts gets a physio's word, once (review of 2026-11-01, training
A6, ai-trainer-ops#338).

Werner's knee lived only in coach memory, and his coach answered with strength
work "to protect the knee" and no referral. For patellofemoral pain, exercise
therapy is first-line, dosed by pain, and a problem that persists is for a
physio (Crossley et al. 2016); on the bike, knee pain under load points to the
fit (saddle height and setback, cleats) and to gearing first. The capture and
the plan prior are in ``domain.athlete_states`` and ``services.plan_priors``;
this is the fixed sentence under the reply when the pain has lasted.

Pure: no database, no clock.
"""

from __future__ import annotations

# From the first mention of the pain in force to one that still says it:
# "about two weeks".
LASTING_DAYS = 14

NOTE = {
    "German": (
        "Schmerzen, die länger als etwa zwei Wochen anhalten, sollte sich eine "
        "Physiotherapeutin oder ein Sportarzt ansehen. Beim Radfahren lohnt oft "
        "auch ein Bike-Fit (Sattelhöhe, Cleats) und eine leichtere Übersetzung."
    ),
    "English": (
        "Pain that lasts longer than about two weeks is worth showing to a "
        "physiotherapist or a sports doctor. On the bike, a bike fit (saddle "
        "height, cleats) and lighter gearing often help too."
    ),
}


def note(german: bool) -> str:
    return NOTE["German" if german else "English"]
