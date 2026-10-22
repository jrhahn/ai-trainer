"""Climb power, worked out by the app (ai-trainer-ops#188).

With the body weight in the profile, the coach still worked out a hill-climb
target from the FTP: for 14 km and 850 m in 45 minutes at 75 kg it said
250–260, 255–265 and 285–300 W, where about 308 W is needed. At 62 kg and
75 kg, 4 of 6 replies missed by more than 5 %.
The owner chose to have the app do the arithmetic and the coach quote it
(ai-trainer-ops#136), as the coach already quotes the power zones.

A steady climb at its average gradient: gravity, rolling resistance and air
drag at the average speed, through the drivetrain. Bike, tyres and position
are not in the profile, so they are fixed here and named in the prompt.
"""

from __future__ import annotations

import math
import re

from .goal_progress import _DISTANCE_UNIT, _QUANTITY, _parse_number

GRAVITY = 9.81
BIKE_KG = 8.0
ROLLING_RESISTANCE = 0.004  # road tyres on tarmac
DRAG_AREA_M2 = 0.35  # seated, hands on the hoods
AIR_DENSITY = 1.1  # a few hundred metres up
DRIVETRAIN = 0.975

# One average gradient describes a climb, not a course that goes up and down:
# the Ötztaler's 238 km and 5500 m are 2.3 %. Steeper than 20 % is a typo.
MIN_GRADIENT = 0.04
MAX_GRADIENT = 0.20
MAX_CLIMB_KM = 40.0
MAX_CLIMBS = 4


def climb_watts(distance_km: float, elevation_m: float, minutes: float, weight_kg: float) -> float:
    """The average power that rides the climb in ``minutes``."""
    speed = distance_km * 1000 / (minutes * 60)
    sine = elevation_m / (distance_km * 1000)
    resistance = (weight_kg + BIKE_KG) * GRAVITY * (sine + ROLLING_RESISTANCE * math.sqrt(1 - sine * sine))
    return (resistance * speed + 0.5 * AIR_DENSITY * DRAG_AREA_M2 * speed**3) / DRIVETRAIN


def climb_minutes(distance_km: float, elevation_m: float, watts: float, weight_kg: float) -> float:
    """The minutes the climb takes at ``watts``: ``climb_watts`` the other way round."""
    fast, slow = 0.1, 2000.0
    for _ in range(60):
        middle = (fast + slow) / 2
        if climb_watts(distance_km, elevation_m, middle, weight_kg) > watts:
            fast = middle
        else:
            slow = middle
    return (fast + slow) / 2


def is_climb(distance_km: float | None, elevation_m: float | None) -> bool:
    if not distance_km or not elevation_m or distance_km > MAX_CLIMB_KM:
        return False
    return MIN_GRADIENT <= elevation_m / (distance_km * 1000) <= MAX_GRADIENT


def stated_climb(text: str | None) -> tuple[float, int] | None:
    """The first sentence or clause in ``text`` whose first distance and first
    climbing make a climb.

    "Bergzeitfahren Puig Major, 14 km, 850 Hm" is (14, 850). In "letzte Woche
    60 km, 1000 Hm gefahren; am Berg 14 km mit 850 Hm" the ride is not paired
    with the climb (review of app #1010). Only the two numbers leave this
    function, so no athlete text reaches the prompt this way.
    """
    # A dot before a space ends a sentence; one between digits is "13.8 km".
    for clause in re.split(r"[;!?\n]|\.\s", text or ""):
        distance = elevation = None
        for match in _QUANTITY.finditer(clause):
            value = _parse_number(match.group(1))
            if re.match(_DISTANCE_UNIT, match.group("unit"), re.IGNORECASE):
                distance = distance or value
            else:
                elevation = elevation or value
        if is_climb(distance, elevation):
            return distance, round(elevation)
    return None


def _row_minutes(distance_km: float, elevation_m: int, weight_kg: float, ftp: int | None) -> list[int]:
    """Whole minutes from the time at 130 % of the FTP to the time at 65 %, in
    about five steps; without an FTP, from 5 W/kg to 2.5 W/kg."""
    strong, easy = (1.3 * ftp, 0.65 * ftp) if ftp else (5.0 * weight_kg, 2.5 * weight_kg)
    fast = climb_minutes(distance_km, elevation_m, strong, weight_kg)
    slow = climb_minutes(distance_km, elevation_m, easy, weight_kg)
    step = next((s for s in (10, 5, 2) if s <= (slow - fast) / 5), 1)
    first = max(step, math.ceil(fast / step) * step)
    return list(range(first, math.floor(slow / step) * step + 1, step)) or [max(1, round(fast))]


def _row(distance_km: float, elevation_m: int, minutes: int, weight_kg: float) -> str:
    watts = climb_watts(distance_km, elevation_m, minutes, weight_kg)
    return f"{minutes} min {watts:.0f} W ({watts / weight_kg:.1f} W/kg)"


def prompt_section(
    climbs: list[tuple[str, float, int]], weight_kg: float | None, ftp: int | None
) -> str:
    """The figures the coach quotes for each ``(label, km, m)`` climb, or "" without
    a weight: the weight is most of the power on a climb."""
    if not weight_kg or not climbs:
        return ""
    lines = [
        f"Climb power, worked out by the app for a body weight of {weight_kg:g} kg and "
        f"a bike of {BIKE_KG:g} kg, on road tyres and seated, at each climb's average "
        "gradient. For a power or time target on these climbs, quote these figures and "
        "say they assume that bike; never work out climb watts yourself:"
    ]
    for label, distance_km, elevation_m in climbs[:MAX_CLIMBS]:
        rows = " · ".join(
            _row(distance_km, elevation_m, minutes, weight_kg)
            for minutes in _row_minutes(distance_km, elevation_m, weight_kg, ftp)
        )
        at_ftp = (
            f" At the FTP of {ftp} W: about {climb_minutes(distance_km, elevation_m, ftp, weight_kg):.0f} min."
            if ftp
            else ""
        )
        gradient = elevation_m / (distance_km * 10)
        lines.append(f"- {label}, {distance_km:g} km with {elevation_m} m ({gradient:.1f} %): {rows}.{at_ftp}")
    return "\n".join(lines)


def known_climbs(
    messages: list[str],
    race_date: str | None,
    race_description: str | None,
    race_events: list[dict],
    today: str,
) -> list[tuple[str, float, int]]:
    """The climbs the app knows of: named in the chat (newest first), the
    profile's race, then the races ahead in the calendar."""
    found: list[tuple[str, float, int]] = []
    for text in messages:
        if climb := stated_climb(text):
            found.append(("a climb named in this chat", *climb))
    if (not race_date or str(race_date)[:10] >= today) and (climb := stated_climb(race_description)):
        found.append((f"the race on {str(race_date)[:10]}" if race_date else "the race in the profile", *climb))
    for event in race_events:
        distance, elevation = event.get("distanceKm"), event.get("elevationM")
        if (event.get("date") or "") >= today and is_climb(distance, elevation):
            found.append((f"the race on {event['date']}", float(distance), int(elevation)))
    unique: dict[tuple[float, int], tuple[str, float, int]] = {}
    for climb in found:
        unique.setdefault(climb[1:], climb)
    return list(unique.values())
