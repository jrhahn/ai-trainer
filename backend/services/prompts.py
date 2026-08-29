"""Prompt construction functions for the AI service.

All functions are pure: they accept typed parameters and return strings.
No LLM client logic, algorithmic computation, or I/O here.
"""

from __future__ import annotations

import json

from . import plan_compliance
from .activity_identity import CYCLING_FAMILY, UNREADABLE_FAMILY, activity_family
from .ride_purpose_question import ATHLETE_STATED_CONFIDENCE
from .training_load import MEASURED_LOAD_SOURCES, format_load, format_load_field
from .analysis import power_zone_boundaries
from .dates import (
    annotate_plan_days,
    app_date_context,
    app_today,
    app_today_iso,
    plan_day_date_labels,
)

# Shared instruction reused wherever the coach may be asked about a compliance
# badge. The badge is scored on the backend and handed to the coach verbatim; it
# used to be browser-only, so the coach reconstructed it from whatever looked
# closest in context and confidently explained the wrong ride (#551).
BADGE_GROUNDING_RULE = (
    "The 'badge:' field on an activity above is the exact compliance badge the "
    "athlete is looking at on that activity's card. When they ask why an activity "
    "is labelled something, answer about the activity whose badge matches — and if "
    "they say 'my last ride' or 'today's ride', that is the newest activity above, "
    "even when an older one carries the same badge. Explain it from that activity's "
    "own badge, score and grading basis. Never contradict a badge, never restate a "
    "'plan linkage:' value as if it were a verdict on execution, and never explain a "
    "badge using another activity's numbers."
)

# Shown only when an estimated load actually appears in the history above.
# Before #579 these sessions carried no load at all and entered the fitness
# chain as rest days, so an hour of strength training read as recovery. They now
# carry an estimate — which is worth far more than a zero and far less than a
# measurement, and the coach has to be told which it is holding.
ESTIMATED_LOAD_RULE = (
    "A load written as 'load ~N' was estimated, not measured: that session had no "
    "power meter, so the figure comes from heart rate or from time on task. Count "
    "it as real fatigue — it is why CTL/ATL/TSB move — but do not quote it as a "
    "precise number, do not compare it against a power-based TSS as if the two "
    "were the same measurement, and never describe cycling form or power progress "
    "on the strength of a non-cycling session's estimated load."
)

# Shared instruction reused by every prompt that names when a planned session
# occurs. Keeping it in one place stops the "next session is today" drift bug
# from creeping back into individual builders (see services/dates.py anchoring).
PLAN_TIMING_GUIDANCE = (
    "When you mention when a planned session occurs, anchor it to the provided date "
    "context and each plan day's weekday/dateLabel/relativeDay fields (for example "
    '"today", "tomorrow", "this Saturday", or "next Tuesday"). The next session is '
    "often days away — never assume it is today, and never compute a weekday from memory."
)


def annotated_plan_json(
    training_plan: list[dict] | None,
    timezone_name: str | None = None,
    *,
    indent: int | None = None,
) -> str:
    """JSON-render plan days with weekday/dateLabel/relativeDay anchors for a prompt.

    Central helper so every plan-consuming builder shows the model the same
    weekday-annotated plan instead of a bare date dump it has to interpret.
    """
    annotated = annotate_plan_days(
        training_plan, app_today(timezone_name=timezone_name)
    )
    return json.dumps(annotated, indent=indent)


def _plan_day_when(day: dict, today) -> str:
    """Human-readable 'when' for a single plan day: dateLabel plus relative day."""
    labels = plan_day_date_labels(day.get("date"), today)
    when = labels.get("dateLabel") or day.get("date", "?")
    relative = labels.get("relativeDay")
    return f"{when} ({relative})" if relative else when


_COACH_VOICE_TRAITS = (
    "You speak like a serious but approachable {sport} coach: warm, personal, plain-spoken, and concise. "
    "Aim for a balanced conversational tone: attentive, natural, calmly confident, and lightly personal; "
    "neither robotic nor performatively friendly. "
    "Do NOT address the athlete by name in most messages. Using someone's name repeatedly feels unnatural in conversation — a real coach does not say 'Good point, Jürgen' or 'I get that, Jürgen' in every other sentence. At most use the name once every several exchanges, and never at the start of a sentence as a filler. Make replies feel specific to their goals, "
    "recent training, mood, or constraints when relevant. When coach memory or recent context includes "
    "a concrete personal detail, weave one of those details in naturally instead of giving generic advice. "
    "Avoid familiar nicknames or endearments. "
    "Do not overdo cheerleading, emojis, exclamation marks, or casual banter. "
    "Short fillers are allowed occasionally (e.g. 'Great question') when they sound natural, "
    "but do not use them as a default opener. Vary sentence openings and avoid repetitive, "
    "template-like starts such as always leading with a recent-ride recap (e.g. "
    "'You did a tough ride yesterday...') unless that detail is truly the most useful place "
    "to begin for this specific message. "
    "Default to 2-4 focused sentences unless the user asks for more detail or the task requires structure; "
    "one warm personal sentence is welcome when it helps the athlete feel seen. "
    "It is okay to say things like 'I get why that feels frustrating' or "
    "'given your goal, I would treat this carefully' when the moment calls for it. "
    "Show real empathy — celebrate their wins, acknowledge struggles with "
    "compassion, and never make them feel judged for missing a session or falling short. "
    "Take the athlete seriously, including when they are joking, frustrated, or sarcastic; "
    "acknowledge the tone lightly when helpful, then return to useful coaching. "
    "Always address the athlete directly using 'you' — for example, "
    "'You have excellent aerobic endurance' not 'The athlete has excellent aerobic endurance'. "
    "Your coaching philosophy: long-term athletic development always overrules short-term gains. "
    "Never sacrifice recovery, health, or sustainable progression for quick wins. "
    "When in doubt, prioritise the athlete's long-term progress over immediate performance. "
    "Be honest and direct when needed, but always frame feedback with kindness and positivity — "
    "clear, respectful, and grounded in what will help them improve."
)

COACH_PERSONA = (
    "You are a knowledgeable cycling coach — warm, personal, respectful, direct, and genuinely "
    "invested in the person you're talking to. "
    + _COACH_VOICE_TRAITS.format(sport="cycling")
)

RUNNING_COACH_PERSONA = (
    "You are a knowledgeable running coach — warm, personal, respectful, direct, and genuinely "
    "invested in the person you're talking to. "
    + _COACH_VOICE_TRAITS.format(sport="running")
)

GENERAL_ENDURANCE_COACH_PERSONA = (
    "You are a knowledgeable multisport endurance coach — warm, personal, respectful, direct, and genuinely "
    "invested in the person you're talking to. "
    + _COACH_VOICE_TRAITS.format(sport="endurance")
)

TRAINING_PLAN_PRINCIPLES = """
Training plan scheduling rules (ALWAYS follow these):
- Schedule long endurance and base rides on Saturday and Sunday unless otherwise constrained by the athlete's profile or preferences.
- Keep weekday sessions short (120 minutes maximum) to fit around work.
- Do not rely on a 'weeklyHours' field; derive realistic weekly volume from the athlete's fitness level
- Make intensity/volume realistic for the athlete's current fitness level and race context, if any.
- When there is an upcoming race date: taper in the final week before the race (reduce volume by ~40%, keep intensity).
- Progressive overload: gradually increase load week-over-week, but include a recovery day after every hard session.
- Never schedule two hard days back-to-back.
- If a rider assessment (FTP/threshold HR) is available, use it to set precise power/HR targets for every workout.
- Account for weather when provided: shorten or reduce intensity on hot days, extend warmups and avoid long exposed sessions on freezing/cold days, and move sessions indoors or swap to recovery/strength when weather is unsafe.
- Weight every weather decision by the athlete's own learned tolerances when they are provided: do not move a session for conditions this athlete demonstrably handles well, and say in the workout description when weather is why a session was placed or shaped that way.
"""


def hard_session_spacing_rules() -> str:
    return (
        "\n\nHard-session spacing rules (actual activity history is authoritative):\n"
        "- Before keeping, recommending, or creating any VO2max, HIIT, threshold, sprint, "
        "or other hard interval session, inspect the recent activity history first, not only "
        "the upcoming plan.\n"
        "- Treat actual VO2max/HIIT/threshold/sprint intervals, very high TSS, very high RPE, "
        "or a ride much harder/longer than planned as a hard session even if the plan label "
        "said recovery or rest.\n"
        "- Strength training is not a complete rest day. MTB/recovery rides count as recovery "
        "only when the available intensity evidence supports that they were genuinely easy.\n"
        "- Do not keep, recommend, or create another VO2max/HIIT/threshold session for tomorrow "
        "or within roughly 48 hours of an actual hard session unless there is a clearly stated "
        "exceptional reason.\n"
        "- If the current upcoming plan violates this spacing, call out the conflict and use "
        "planUpdates to replace the near-term hard session with endurance, recovery, or rest, "
        "or move the intensity to a later feasible day.\n"
        "- A positive TSB can support endurance or controlled aerobic work, but it does not by "
        "itself justify back-to-back or near-back-to-back VO2max/HIIT sessions."
    )


# ---------------------------------------------------------------------------
# analyse_strava_activities prompts
# ---------------------------------------------------------------------------


def analyse_activities_system(
    sport_type: str = "cycling", user_ftp: int | None = None
) -> str:
    """Return the system prompt for activity analysis.

    When *sport_type* is ``"running"`` (or any non-cycling type) a running-
    specific prompt is returned that omits power-based FTP and uses HR-based
    thresholds instead.
    """
    sport_key = sport_type.lower()
    is_running = sport_key in ("running", "run")
    is_cycling = sport_key in (
        "cycling",
        "ride",
        "virtualride",
        "virtual_ride",
        "ebikeride",
        "e-bike ride",
        "mountainbikeride",
        "gravelride",
    )

    if is_running:
        persona = RUNNING_COACH_PERSONA
        activity_noun = "run"
        activities_noun = "runs"
        ftp_field = '- "estimatedFTP": always null for running (no power data)\n'
        category_section = (
            "Run categories (use HR-based zones when power is unavailable):\n"
            "- recovery: easy jogging, HR < 65% max HR\n"
            "- endurance: steady aerobic run, HR 65–78% max HR\n"
            "- tempo: comfortably hard, HR ~79–87% max HR (lactate threshold)\n"
            "- interval_threshold: hard 5–12 min efforts at ~88–95% max HR\n"
            "- interval_vo2max: very hard 2–5 min efforts at > 95% max HR\n"
            "- interval_sprints: short < 2 min sprint efforts at near max HR\n\n"
        )
        rider_type_section = (
            "Runner-type guidelines:\n"
            "- timetrial: strong sustained pace, low variability, long threshold efforts\n"
            "- sprinter: high speed over short distances, high HR variability\n"
            "- climber: strong on elevation, longer sustained efforts\n"
            "- endurance: long runs, moderate intensity, high volume\n"
            "- allrounder: balanced across metrics"
        )
        notes_example = (
            "Example: 'You show strong aerobic endurance with consistent HR throughout your longer runs. "
            "Your threshold HR looks solid…'"
        )
        last_ride_key = "run"
    elif is_cycling:
        persona = COACH_PERSONA
        activity_noun = "ride"
        activities_noun = "rides"
        ftp_field = (
            '- "estimatedFTP": always null — FTP is never estimated from activity data; '
            "set this field to null"
            "\n"
        )
        category_section = (
            "Ride categories:\n"
            "- unknown: insufficient stream data or FTP to classify reliably\n"
            "- short_easy_spin: short low/medium-intensity ride (<30 min), likely recovery, commute, warmup, or aborted ride\n"
            "- short_hard_effort: short high-intensity ride (<30 min) without clear full workout structure\n"
            "- recovery: avg power < 60% FTP\n"
            "- endurance: avg power 60–75% FTP, no distinct intervals\n"
            "- tempo: avg power ~76–85% FTP, no distinct intervals\n"
            "- interval_sweetspot: 10–20 min efforts at 88–95% FTP\n"
            "- interval_threshold: ~5–12 min efforts at 95–105% FTP\n"
            "- interval_vo2max: 2–5 min efforts at 106–130% FTP\n"
            "- interval_sprints: < 2 min efforts at > 130% FTP\n\n"
        )
        rider_type_section = (
            "Rider-type guidelines:\n"
            "- timetrial: strong sustained power, low variability, long average efforts\n"
            "- sprinter: high max power, shorter efforts, high power variability\n"
            "- climber: high elevation gain per km, longer sustained efforts at moderate power\n"
            "- endurance: long rides, moderate intensity, high volume\n"
            "- allrounder: balanced across metrics"
        )
        notes_example = (
            "Example: 'You show strong sustained power over long efforts, which marks you as a time-trial type rider. "
            "Your aerobic base looks solid…'"
        )
        last_ride_key = "ride"
    else:
        persona = GENERAL_ENDURANCE_COACH_PERSONA
        activity_noun = "activity"
        activities_noun = "activities"
        ftp_field = (
            '- "estimatedFTP": always null unless the activities are clearly cycling with power; '
            "for hiking, walking, strength, yoga, or mixed activity batches set this field to null\n"
        )
        category_section = (
            "Activity categories:\n"
            "- recovery: deliberately easy activity, low HR, low muscular strain\n"
            "- endurance: steady aerobic work appropriate to the sport\n"
            "- tempo: sustained moderate-to-hard aerobic effort\n"
            "- strength: gym, bodyweight, or resistance-training session\n"
            "- hike_walk: hiking or walking activity; assess duration, elevation, and HR, not cycling power\n"
            "- commute_transport: transport-oriented activity rather than a planned workout\n"
            "- mixed: multiple activity types in the same batch\n"
            "- unknown: insufficient data to classify reliably\n\n"
        )
        rider_type_section = (
            "Athlete-type guidelines:\n"
            "- endurance: long aerobic activities, high consistency, good fatigue resistance\n"
            "- climber: strong elevation-heavy activity profile\n"
            "- sprinter: short powerful efforts or high-intensity bursts\n"
            "- timetrial: strong sustained efforts at steady intensity\n"
            "- allrounder: balanced across activity types and intensities"
        )
        notes_example = (
            "Example: 'Your recent hiking volume adds useful low-intensity aerobic load, but it should not be "
            "treated like a cycling interval session…'"
        )
        last_ride_key = "activity"

    return (
        f"{persona} Analyse the provided activities and return a JSON assessment.\n"
        "Return ONLY a valid JSON object with these fields "
        "(all keys double-quoted, numeric values must be plain numbers with no units):\n"
        f"{ftp_field}"
        '- "riderType": one of "timetrial", "sprinter", "climber", "allrounder", "endurance"\n'
        "- \"notes\": a concise overall assessment addressed directly to the athlete using 'you'. "
        f"Mention their strengths, rider type, and key observations from their {activities_noun}. "
        f"{notes_example}\n"
        f'- "lastRideFeedback": a standalone 2-4 sentence coach note about the SINGLE MOST RECENT {last_ride_key} only '
        f"(the one with the latest start_date). Write it as a card the athlete reads first thing on their dashboard. "
        f"Cover: (1) what type of {last_ride_key} it was (category) and key numbers, "
        "(2) how the effort looked — "
        + (
            "HR response and pace consistency or drift if data available, "
            if is_running
            else "power consistency and HR response or drift if data available, "
        )
        + "(3) one concrete recommendation for the next training session. "
        "Be warm, personal, and specific — use their actual numbers.\n"
        f'- "rideInsights": a JSON array — one object per {activity_noun} — with keys '
        '"id" (the activity id as a number), "name" (activity name string), and "note" (a 2-4 sentence '
        f"coach note addressed to the athlete). For each {activity_noun}: state its category, "
        "comment on the effort quality (HR drift if data available), and give one "
        "concrete takeaway. Also include 1-2 specific recommendations for the athlete's next training "
        "session based on what you observed. Be empathetic and personal — reference their specific numbers.\n"
        '- "loginSummary": a compact dashboard coaching brief addressed directly to the athlete. '
        "The coach decides which information is important and which details are trivial. Do not force "
        "fixed categories. Write one short intro sentence, then 2-4 bullet points. Each bullet must "
        "start with a short coach-chosen label followed by a colon, for example "
        '"- Fatigue: ..." or "- Next session: ...". Include only the most useful takeaways from '
        f"the recent {activities_noun}, such as volume, effort quality, FTP/fitness signals, plan "
        "alignment, fatigue, or next actions when they genuinely matter. Omit categories with no "
        "meaningful signal. Be specific, warm, and encouraging. "
        f"{PLAN_TIMING_GUIDANCE}\n"
        '- "planUpdates": optional array of training day updates for the upcoming plan based on what '
        f"you observed in the {activities_noun}. Only include updates that are genuinely warranted (e.g. add recovery "
        "if athlete shows fatigue/HR drift, increase intensity if athlete is clearly above their current "
        "targets, or reduce/swap workouts when recent hot/cold weather likely increased strain). "
        'Each update: {"date": "<ISO date>", "workoutType": "<type>", "title": '
        '"<string>", "description": "<string>", "durationMinutes": <int>}. '
        "If no updates are needed, omit this field or set it to [].\n\n"
        f"{category_section}"
        f"{rider_type_section}"
    )


# Power fields that must never be dumped *unlabeled* alongside the raw activity
# JSON — they are re-presented, named and unit-tagged, in the canonical
# power-metrics block below so the model cannot cite a bare/ambiguous number
# (#467). ``average_watts`` is the provider average; ``weighted_average_watts``
# is Normalized Power; ``max_watts`` is peak power.
_UNLABELED_POWER_FIELDS = ("average_watts", "weighted_average_watts", "max_watts")


_TIME_IN_ZONE_LABELS = {
    "z1_secs": "Z1",
    "z2_secs": "Z2",
    "z3_secs": "Z3",
    "z4_secs": "Z4",
    "z5_secs": "Z5",
    "z6_secs": "Z6",
    "z7_secs": "Z7",
}


def power_zones_block(user_ftp: int | None) -> str:
    """Render the athlete's absolute power-zone boundaries from their FTP.

    Grounds every zone/intensity claim in explicit watt boundaries so the coach
    stops inventing narratives like "above your Zone 2 ceiling" for a ride whose
    average sits well inside Z2 (#468). Returns "" without a usable FTP.
    """
    if not user_ftp or user_ftp <= 0:
        return ""
    # ftp > 0 here, so power_zone_boundaries always returns the 7 zones.
    zones = power_zone_boundaries(float(user_ftp))
    parts: list[str] = []
    for z in zones:
        low, high = z["low_w"], z["high_w"]
        if low is None:
            rng = f"<{high} W"
        elif high is None:
            rng = f">{low} W"
        else:
            rng = f"{low}–{high} W"
        parts.append(f"{z['zone']} {z['name']} {rng}")
    # Z2 upper edge is the "endurance / Zone 2 ceiling" the coach kept misusing.
    z2_ceiling = zones[1]["high_w"]
    return (
        f"\n\nYour power zones at FTP {user_ftp} W "
        f"(Zone 2 / endurance ceiling = {z2_ceiling} W): "
        + " · ".join(parts)
    )


def _time_in_zone_summary(tiz: dict | None) -> str:
    """Compact 'Z2 210 min · Z3 15 min' summary; "" when no zone has time."""
    if not tiz:
        return ""
    parts = [
        f"{label} {round(tiz[key] / 60)} min"
        for key, label in _TIME_IN_ZONE_LABELS.items()
        if tiz.get(key, 0) > 0
    ]
    return " · ".join(parts)


def activity_power_metrics_block(
    activities: list[dict],
    user_ftp: int | None = None,
    time_in_zone_by_id: dict[str, dict] | None = None,
) -> str:
    """One canonical, labeled power/load line per activity.

    The raw activity dump carries ``average_watts`` (avg) and
    ``weighted_average_watts`` (NP) with nothing marking which is authoritative,
    so the coach narrated the wrong one — e.g. citing our lossy stream-mean
    "averaging 221W" against the provider's real 201 W avg / 215 W NP (#467).
    This hands the model a single labeled block instead, and instructs it to
    cite only these figures. When ``time_in_zone_by_id`` maps an activity id to
    its per-zone seconds, a grounded time-in-zone line is appended so intensity
    claims cite measured time, not a guessed zone (#468). Returns "" when no
    activity has power data.
    """
    time_in_zone_by_id = time_in_zone_by_id or {}
    lines: list[str] = []
    for activity in activities:
        avg = activity.get("average_watts")
        np = activity.get("weighted_average_watts")
        peak = activity.get("max_watts")
        if avg is None and np is None and peak is None:
            continue
        parts: list[str] = []
        if avg is not None:
            parts.append(f"average power {round(avg)} W")
        if np is not None:
            parts.append(f"normalized power {round(np)} W")
            if user_ftp and user_ftp > 0:
                parts.append(f"intensity factor {round(np / user_ftp, 3)}")
        if peak is not None:
            parts.append(f"peak power {round(peak)} W")
        name = activity.get("name") or activity.get("type") or "activity"
        day = (activity.get("start_date_local") or activity.get("start_date") or "")[
            :10
        ]
        label = f"{name} ({day})" if day else str(name)
        lines.append(f"- {label}: " + " · ".join(parts))
        tiz = _time_in_zone_summary(time_in_zone_by_id.get(str(activity.get("id"))))
        if tiz:
            lines.append(f"    time in zones: {tiz}")
    if not lines:
        return ""
    return (
        "\n\nPer-activity power & load (authoritative — cite these labeled "
        "figures verbatim when discussing power or load; never recompute, "
        "re-average, or invent a number, and always name the metric with its "
        "unit, e.g. 'average power 201 W', never a bare '201'):\n"
        + "\n".join(lines)
    )


def analyse_activities_user(
    activities: list[dict],
    computed_section: str,
    ride_analyses_section: str,
    sport_type: str = "cycling",
    training_plan: list[dict] | None = None,
    timezone_name: str | None = None,
    user_ftp: int | None = None,
    time_in_zone_by_id: dict[str, dict] | None = None,
) -> str:
    sport_key = sport_type.lower()
    is_running = sport_key in ("running", "run")
    is_cycling = sport_key in (
        "cycling",
        "ride",
        "virtualride",
        "virtual_ride",
        "ebikeride",
        "e-bike ride",
        "mountainbikeride",
        "gravelride",
    )
    activities_noun = (
        "runs"
        if is_running
        else ("Strava rides" if is_cycling else "Strava activities")
    )
    ftp_note = "Set estimatedFTP to null. "
    plan_section = ""
    if training_plan:
        plan_section = (
            f"\n\nCurrent training plan (use for plan alignment in loginSummary; "
            f"each day carries weekday/dateLabel/relativeDay anchors):\n"
            f"{annotated_plan_json(training_plan, timezone_name, indent=2)}"
        )
    # Present power/load once, labeled, in the canonical block below — strip the
    # bare, unlabeled power fields from the raw dump so the model can't cite an
    # ambiguous or duplicate figure (#467).
    dump_activities = [
        {k: v for k, v in activity.items() if k not in _UNLABELED_POWER_FIELDS}
        for activity in activities
    ]
    metrics_block = activity_power_metrics_block(
        activities, user_ftp, time_in_zone_by_id
    )
    # Power zones are FTP-based and only meaningful for cycling power data.
    zones_block = "" if is_running else power_zones_block(user_ftp)
    zone_guidance = (
        ""
        if is_running
        else (
            "Characterize intensity or training zones ONLY using the power-zone "
            "boundaries and per-activity time-in-zone provided above. Never claim a "
            "ride was above or below a zone (e.g. 'above your Zone 2 ceiling') unless "
            "the ride's own average or normalized power actually crosses that zone's "
            "stated watt boundary — cite the boundary and the figure. A ride with "
            "average and normalized power inside Z2 is an endurance ride, not an "
            "above-Z2 effort. "
        )
    )
    return (
        f"{app_date_context(timezone_name=timezone_name)}\n\n"
        f"Last {len(activities)} {activities_noun}:\n{json.dumps(dump_activities, indent=2)}"
        f"{metrics_block}"
        f"{zones_block}"
        f"{computed_section}"
        f"{ride_analyses_section}"
        f"{plan_section}\n\n"
        f"Assess my fitness. {ftp_note}"
        "When you state any power or training-load figure, cite only the labeled "
        "'Per-activity power & load' values above and name the metric with its "
        "unit — never a bare number, and never recompute or average a figure yourself. "
        f"{zone_guidance}"
        "Use each activity's sport_type/type when writing rideInsights and appropriate planUpdates; "
        "do not describe non-cycling activities as rides."
    )


def analyse_activities_computed_section(
    computed_ftp: int | None,
    max_heart_rate: int | None,
    computed_hr_zones: dict | None,
) -> str:
    """Build the contextual block describing the athlete's entered FTP and HR metrics.

    Returns an empty string when no metrics are available.
    """
    section = ""
    if computed_ftp is not None:
        section += (
            f"\nAthlete's entered FTP: {computed_ftp} W "
            "(set directly by the athlete — use this value verbatim)"
        )
    if max_heart_rate is not None:
        section += f"\nMax heart rate provided by athlete: {max_heart_rate} bpm"
    if computed_hr_zones is not None:
        zones_str = ", ".join(
            f"Zone {i}: {z['low']}–{z['high']} bpm"
            for i, z in enumerate(computed_hr_zones.values(), 1)
        )
        section += f"\nHR training zones: {zones_str}"
    return section


# ---------------------------------------------------------------------------
# generate_training_plan prompts
# ---------------------------------------------------------------------------


def plan_allocation_rule() -> str:
    """Plan for what the athlete trains for, not only for progression (#602).

    The conversation layer has been athlete-model-first since #562/#565/#597 and
    the planner never was. It asked whether the athlete could physiologically
    take another threshold session and, being right about that, put one in the
    week — 48 h after the last one, in 34 °C, two days before the long off-road
    day the athlete actually trains for. The coach then talked them out of it in
    chat, which is the mismatch in one sentence: the plan and the conversation
    were optimizing different things.

    So this rule changes the question. Not *can they handle it* but *is this the
    best use of the freshness they have* — and the answer comes from the board in
    the athlete's own section, not from here. Three things it has to hold:

    * **This is not an instruction to train less.** Reallocating freshness is the
      whole point; a planner that read this as "go easier" would be a worse
      planner and a more annoying one.
    * **Off-bike work is a real option.** A strength day was never on any board
      the planner could see, which is why the only way to not schedule intervals
      was to schedule nothing.
    * **The reason given to the athlete has to be theirs.** "Avoid unnecessary
      fatigue" is a true sentence about a stranger. The same decision explained
      as protecting the next long day is the same plan and a different coach —
      the #565 chain, applied to ``workoutPurpose``.
    """
    return (
        "\n\nAllocating the athlete's freshness (read with their objective section):\n"
        "- The question for every day is NOT 'can this athlete physiologically "
        "handle a hard session?' — it is 'is a hard session the highest-value use "
        "of the freshness they have?'. Those differ exactly when the athlete "
        "trains for something other than a bigger number.\n"
        "- When a ranked day board is given, treat it as the priority order for "
        "what a day should BE. It already accounts for this athlete's own "
        "weights, the heat, and what their freshness is being saved for. Deviate "
        "from it when the calendar, availability or hard-session spacing demand "
        "it — those still win — but not merely to accumulate load.\n"
        "- Off-bike strength and mobility work is a full option, not a filler. It "
        "is real training that costs almost nothing in riding freshness, which "
        "makes it the right answer on a hot day 48 h after a hard session and "
        "ahead of a long weekend ride. Schedule it as \"strength\" with a concrete "
        "session, not as a vague note.\n"
        "- This is not an instruction to train less. Total load is not the thing "
        "being reduced — it is being spent somewhere else. If you take intensity "
        "out of one day, the plan still has to add up over the fortnight.\n"
        "- Explain the choice in the athlete's terms in \"workoutPurpose\". Do NOT "
        "write 'avoid unnecessary fatigue', 'keep systemic load low' or 'protect "
        "the adaptation' — those describe the mechanism and say nothing about "
        "them. Write what the day buys them: keeping the next long off-road day "
        "sharp rather than merely survivable, arriving at the event able to "
        "race it, still enjoying the last descent after four hours. When no "
        "objective is given, fall back to plain physiology — never invent one."
    )


def planner_athlete_model_section(
    motivation: dict | None = None,
    identity: dict | None = None,
    allocation: dict | None = None,
) -> str:
    """Everything the planner needs to know about *who it is planning for* (#602).

    One composed block rather than three parameters threaded through every plan
    entry point, because these are never useful apart: an objective with no board
    is a preference the planner cannot act on, and a board with no objective is
    five numbers with no reason attached.
    """
    parts = [
        motivation_model_section(motivation).strip(),
        rider_identity_section(identity).strip(),
        freshness_allocation_section(allocation).strip(),
    ]
    body = "\n\n".join(part for part in parts if part)
    return f"\n\n{body}" if body else ""


# How many day options reach the plan prompt. All five is barely more expensive
# than three and the ones at the bottom carry the trade-off: a board that stops
# after the winners does not show what was given up.
_ALLOCATION_OPTIONS_SHOWN = 5


def freshness_allocation_section(allocation: dict | None) -> str:
    """The day board, with every deduction named (#602).

    Both the base score and the two deductions are shown per option on purpose,
    for the reason #564 shows its two sub-scores: a day that lost to the weather
    and a day that lost to the weekend lost for different reasons, and a coach
    that cannot tell the athlete which one is not explaining anything.
    """
    if not allocation or not allocation.get("options"):
        return ""

    # Imported here rather than at module scope: this module is imported by
    # nearly everything, and the allocation service reaches the learning stack.
    from services.freshness_allocation import band, demand_label

    values = allocation.get("recovery_value") or {}
    worth = "; ".join(
        f"{demand_label(demand)}: {band(float(value))}"
        for demand, value in sorted(values.items(), key=lambda kv: -kv[1])
    )

    lines = [
        "\n\nWhat this athlete's freshness is worth spending on "
        "(derived from their own objective and weights, not from physiology):",
    ]
    if worth:
        lines.append(f"- Freshness is worth most for — {worth}")

    lines.append(
        "Ranked kinds of day for this athlete right now"
        + (
            " on a day at or above 29 °C"
            if allocation.get("hot")
            else ""
        )
        + " (score = their own weighted value, minus heat, minus the freshness a "
        "higher-valued demand wanted):"
    )
    for option in allocation["options"][:_ALLOCATION_OPTIONS_SHOWN]:
        detail = f"score {option.get('score')} (value {option.get('base')}"
        if option.get("heat_cost"):
            detail += f", −{option['heat_cost']} heat"
        if option.get("freshness_cost"):
            detail += (
                f", −{option['freshness_cost']} freshness owed to "
                f"{demand_label(str(option.get('competes_with') or ''))}"
            )
        detail += ")"
        lines.append(
            f"- {option.get('label')} [{option.get('prescription')}, "
            f"{option.get('modality')}]: {detail}"
        )

    tolerance = allocation.get("heat_tolerance")
    if tolerance:
        lines.append(
            f"- The heat deduction above is already scaled for this athlete being "
            f"heat-{tolerance}; do not discount hot days a second time."
        )
    lines.append(
        "This ranks what a day is best spent ON. It does not know the calendar, "
        "so you still place the sessions — availability, race dates and "
        "hard-session spacing override it."
    )
    return "\n".join(lines)


def generate_plan_system() -> str:
    return (
        f"{COACH_PERSONA} Generate a 14-day training plan as JSON.\n"
        'Return ONLY a valid JSON object with a "plan" array of training days. '
        "All keys must be double-quoted. All numeric fields must be plain numbers with no units.\n"
        'Each day must have: "date" (ISO date string starting from today), '
        '"workoutType" (one of: "rest","endurance","intervals","tempo","race",'
        '"recovery","strength"), "title" (string), '
        '"description" (a 2-4 sentence summary of the session using the athlete\'s actual FTP and '
        "threshold HR values to state exact power/HR targets — never write percentages alone, always "
        "translate them to absolute numbers, e.g. 'Ride for 90 min at 195–220 W (Zone 2, 75–85% of "
        "your 260 W FTP). Keep HR under 148 bpm. The goal is fat oxidation and aerobic base building "
        "— you should be able to hold a conversation throughout.'), "
        '"durationMinutes" (integer), '
        '"workoutPurpose" (1-2 sentences on what this session earns the athlete, written '
        "forwards: the capability they will have afterwards that they do not have now. "
        "State the adaptation, then what it lets them do. Do NOT justify the session's "
        "placement in the plan or recap the days around it — the athlete is about to ride "
        "it, not audit the schedule. E.g. 'This tempo block trains your body to clear "
        "lactate faster, so the same climbing pace costs you less. Over a few weeks it is "
        "what lets you hold your threshold effort to the top instead of fading.'), "
        '"keyFocusPoints" (array of 3-5 short coaching-cue strings, each beginning with an action '
        'verb — e.g. ["Keep cadence between 88-95 rpm throughout", "HR must stay below 158 bpm '
        '(Zone 3); back off if it creeps higher", "Breathe rhythmically — aim for a 3-in/2-out '
        'pattern on climbs"]).\n'
        'Optional fields: "durationMinMinutes" and "durationMaxMinutes" (integers) to '
        "prescribe a duration *window* instead of a single value — use these for endurance "
        "and base sessions that are naturally a range (e.g. a 2.5–3h endurance ride: "
        "durationMinMinutes 150, durationMaxMinutes 180), and set durationMinutes to the "
        "midpoint. Keep structured interval/threshold/VO2 sessions to a single durationMinutes. "
        '"targetPower" (object with "low" and "high" integer fields in watts), '
        '"targetHeartRate" (object with "low" and "high" integer fields in bpm), '
        '"intervals" (array of objects with "duration" (integer seconds), '
        '"power" (integer watts), "rest" (integer seconds)).\n'
        f"{TRAINING_PLAN_PRINCIPLES}"
        f"{plan_allocation_rule()}\n"
        "Workout type guidance:\n"
        "- If there is an upcoming race in the athlete profile or race calendar, include race-specific workouts and a taper week\n"
        "- If there is no upcoming race, build a balanced general-fitness plan with endurance, strength, consistency, and recovery\n"
        "- Tailor workout types to the rider type "
        "(e.g. more sprints for sprinters, more climbs for climbers, sustained tempo for TT riders)"
    )


def race_events_context_section(race_events: list[dict] | None) -> str:
    if not race_events:
        return ""

    lines = ["Race calendar (fixed athlete events to plan around):"]
    for event in race_events:
        date = event.get("date", "")
        start_time = event.get("startTime") or event.get("start_time")
        distance = event.get("distanceKm") or event.get("distance_km")
        elevation = event.get("elevationM") or event.get("elevation_m")
        time_part = f" at {start_time}" if start_time else ""
        lines.append(
            f"- {date}{time_part}: {distance:g} km with {elevation} m climbing"
            if isinstance(distance, (int, float)) and elevation is not None
            else f"- {json.dumps(event)}"
        )
    lines.append(
        "Treat these events as real calendar commitments. Build race-specific preparation, "
        "terrain-specific work, and taper/recovery around them."
    )
    return "\n".join(lines)


def race_profile_context_section(profile: dict) -> str:
    """Return race context from the profile when a race was entered during setup."""
    race_date = profile.get("raceDate") or profile.get("race_date")
    race_description = profile.get("raceDescription") or profile.get("race_description")
    if not race_date and not race_description:
        return ""

    lines = [
        "Profile race context:",
    ]
    if race_date:
        lines.append(
            f"- Race date: {race_date} (use this for race-specific preparation and taper timing)."
        )
    if race_description:
        lines.append(
            f"- Race description: {race_description} (treat this as event context for training decisions)."
        )
    return "\n".join(lines)


def generate_plan_user(
    profile: dict,
    today: str,
    assessment_section: str,
    metrics_history_section: str = "",
    weather_context_section: str = "",
    race_events_section: str = "",
    athlete_model_section: str = "",
) -> str:
    race_profile_section = race_profile_context_section(profile)
    race_profile_section = f"\n{race_profile_section}" if race_profile_section else ""
    metrics_section = f"\n{metrics_history_section}" if metrics_history_section else ""
    weather_section = f"\n{weather_context_section}" if weather_context_section else ""
    events_section = f"\n{race_events_section}" if race_events_section else ""
    return (
        f"Today's date: {today}\n"
        f"Profile: {json.dumps(profile)}{race_profile_section}{assessment_section}{metrics_section}{weather_section}{events_section}{athlete_model_section}\n"
        "Generate a 14-day training plan starting from today that reflects the athlete's "
        "actual fitness level from recent rides and any upcoming race context."
    )


# ---------------------------------------------------------------------------
# adapt_training_plan prompts
# ---------------------------------------------------------------------------


def adapt_plan_system() -> str:
    return (
        f"{COACH_PERSONA} Adapt the remaining training plan based on recent workout feedback.\n"
        'Return ONLY a valid JSON object with an "updatedDays" array. '
        "All keys must be double-quoted. All numeric fields must be plain numbers with no units. "
        "For future days keep the same date fields. "
        "For any past incomplete days (date before today), reschedule them to upcoming dates "
        "starting from today, distributing the sessions sensibly without overloading consecutive days.\n"
        "Hard athlete constraints are non-negotiable: if the profile, coach memory, "
        "athlete context, or recent conversation says the athlete is unavailable on a "
        "specific date or weekday, do not schedule training there even when it would be "
        "physiologically optimal. Keep that day as rest or unavailable, and move the "
        "training stimulus to the best available day instead.\n"
        f"{hard_session_spacing_rules()}\n"
        "Each updated day must include all required TrainingDay fields: "
        '"date", "workoutType", "title", "durationMinutes".\n'
        "For endurance/base sessions that are naturally a window you may also set "
        '"durationMinMinutes" and "durationMaxMinutes" (integers, with durationMinutes as '
        "the midpoint); keep structured interval sessions to a single durationMinutes.\n"
        "Each updated day must also include: "
        '"description" (a 2-4 sentence summary using the athlete\'s actual FTP and threshold HR to '
        "state exact power/HR targets — always translate percentages to absolute numbers), "
        '"workoutPurpose" (1-2 sentences on what the session earns the athlete, written '
        "forwards — the capability they will have afterwards, not a justification of where "
        "it sits in the adapted plan), "
        '"keyFocusPoints" (array of 3-5 coaching-cue strings, each starting with an action verb).\n'
        f"{TRAINING_PLAN_PRINCIPLES}"
        f"{plan_allocation_rule()}\n"
        "Use TSB to guide adaptation: TSB < −20 suggests accumulated fatigue, prioritise recovery; "
        "TSB > +10 before a key workout suggests freshness, intensity can be increased. "
        "A positive TSB says freshness is available; it does not say what to spend it on — "
        "the freshness-allocation rules above decide that."
    )


def adapt_plan_user(
    profile: dict,
    today: str,
    recent_feedback: list[dict],
    incomplete_days: list[dict],
    rider_assessment: dict | None = None,
    training_load: dict | None = None,
    taper_days_remaining: int | None = None,
    metrics_history_section: str = "",
    weather_context_section: str = "",
    race_events_section: str = "",
    athlete_model_section: str = "",
) -> str:
    assessment_section = (
        f"\nRider assessment: {json.dumps(rider_assessment)}"
        if rider_assessment
        else ""
    )
    load_section = ""
    if training_load:
        load_section = (
            f"\nTraining load (from plan): CTL={training_load.get('ctl')} "
            f"ATL={training_load.get('atl')} TSB={training_load.get('tsb')}"
        )
    metrics_section = f"\n{metrics_history_section}" if metrics_history_section else ""
    weather_section = f"\n{weather_context_section}" if weather_context_section else ""
    events_section = f"\n{race_events_section}" if race_events_section else ""
    race_profile_section = race_profile_context_section(profile)
    race_profile_section = f"\n{race_profile_section}" if race_profile_section else ""
    taper_section = ""
    if taper_days_remaining is not None:
        taper_section = (
            f"\n⚠️  TAPER ALERT: Race is in {taper_days_remaining} day(s). "
            "You MUST restructure the remaining plan as a race taper: "
            "reduce total volume by ~40% compared to the previous training week, "
            "keep intensity (one short sharpener at race pace is fine), "
            "add extra rest/recovery days, and ensure the athlete arrives at the start "
            "line fresh (target TSB +5 to +15)."
        )
    past_incomplete_count = sum(1 for d in incomplete_days if d.get("date", "") < today)
    stale_note = (
        f"NOTE: {past_incomplete_count} session(s) are past their scheduled date and have not "
        "been completed. Reschedule these to upcoming dates (starting from today) so the athlete "
        "always has current sessions ahead of them.\n"
        if past_incomplete_count > 0
        else ""
    )
    return (
        f"Today's date: {today}\n"
        f"Profile: {json.dumps(profile)}{race_profile_section}{assessment_section}{load_section}{metrics_section}{weather_section}{events_section}{athlete_model_section}{taper_section}\n"
        f"Recent feedback: {json.dumps(recent_feedback)}\n"
        f"Remaining plan days: {json.dumps(incomplete_days)}\n"
        + stale_note
        + "Adapt the remaining days based on the feedback. Return the full updated days array."
    )


def _day_brief(day: dict | None) -> str:
    """A compact one-line description of a plan day for a change diff (#439)."""
    if not day:
        return "rest / no session"
    parts = [str(day.get("workoutType") or day.get("title") or "session")]
    title = day.get("title")
    if title and title != day.get("workoutType"):
        parts.append(f'"{title}"')
    duration = day.get("durationMinutes")
    if duration:
        parts.append(f"{duration} min")
    return " ".join(parts)


def plan_change_summary_system() -> str:
    """System prompt: narrate a coach run's plan changes to the athlete (#439)."""
    return (
        f"{COACH_PERSONA} You have just reviewed and adjusted this athlete's "
        "training plan. Write a short, personal note telling them what you changed "
        "and — most importantly — why the new plan is better for them.\n"
        "Return ONLY a valid JSON object with exactly two keys:\n"
        '"summary": one short first-person paragraph (2-4 sentences) addressed to '
        "the athlete. Lead with what changed, then why it helps them (recovery, "
        "freshness, building fitness, an upcoming race). Be concrete and reference "
        "specific days or sessions, but do NOT list every day mechanically and do "
        "not use markdown headings.\n"
        '"days": an array of {"date", "reason"} objects — one per changed day, each '
        "a single concise clause explaining that day's change. Use the exact dates "
        "from the diff.\n"
        "Only describe changes present in the diff; never invent changes. If a day "
        "was merely rescheduled, say so plainly."
    )


def plan_change_summary_user(
    changes: list[dict],
    profile: dict,
    *,
    run_context: str,
    rider_assessment: dict | None = None,
    training_load_section: str = "",
    weather_context_section: str = "",
) -> str:
    """User prompt carrying the per-day old→new diff and athlete context (#439).

    ``weather_context_section`` is the forecast and learned tolerances the run
    itself acted on (#495). Without it the narration cannot tell the athlete that a
    session moved because of a 38 °C day, and a weather-driven change reads as the
    plan churning for no reason.
    """
    diff_lines = "\n".join(
        f"- {c.get('date')}: {_day_brief(c.get('old_day'))} → "
        f"{_day_brief(c.get('new_day'))}"
        for c in changes
    )
    assessment_section = (
        f"\nRider assessment: {json.dumps(rider_assessment)}"
        if rider_assessment
        else ""
    )
    load_section = f"\n{training_load_section}" if training_load_section else ""
    weather_section = (
        f"\n{weather_context_section}" if weather_context_section else ""
    )
    weather_rule = (
        "\nIf a change lines up with the forecast above, say so plainly and name the "
        "condition — but only when the weather genuinely explains it; never invent a "
        "weather reason for an unrelated change."
        if weather_context_section
        else ""
    )
    return (
        f"Context: {run_context}\n"
        f"Athlete profile: {json.dumps(profile)}"
        f"{assessment_section}{load_section}{weather_section}\n"
        "Changes you just made (old → new):\n"
        f"{diff_lines}\n"
        f"Write the summary and per-day reasons as instructed.{weather_rule}"
    )


# ---------------------------------------------------------------------------
# ask_trainer prompts
# ---------------------------------------------------------------------------


def ask_trainer_assessment_section(
    rider_assessment: dict | None, current_ftp: int | None = None
) -> str:
    """Build the rider-assessment section string. Returns '' when falsy."""
    if not rider_assessment:
        return ""
    rider_type = rider_assessment.get("riderType", "")
    notes = rider_assessment.get("notes", "")
    assessment_lines = [f"- Rider type: {rider_type}"]
    if current_ftp:
        assessment_lines.append(f"- FTP: {current_ftp} W")
    if notes:
        assessment_lines.append(f"- Assessment notes: {notes}")
    return (
        "\n\nRider assessment from recent Strava analysis:\n"
        + "\n".join(assessment_lines)
        + "\nUse this to give personalised advice that matches the athlete's strengths and riding style."
    )


def ask_trainer_workout_section(context_workout: dict | None) -> str:
    """Build the context-workout section string. Returns '' when None."""
    if context_workout is None:
        return ""
    return (
        f"\n\nThe athlete is currently viewing this specific workout:\n"
        f"{json.dumps(context_workout, indent=2)}"
        "\nWhen the athlete refers to 'this workout', 'today's session', or similar, "
        "they mean the workout above. "
        "IMPORTANT: whenever your coaching response proposes ANY change or adjustment to this "
        "workout (e.g. shorter duration, lower intensity, different structure), you MUST include "
        "the updated workout in planUpdates so the card is immediately refreshed. Do not merely "
        "describe the change in text — always materialise it as a planUpdates entry."
    )


def ask_trainer_plan_updates_rule(context_workout: dict | None) -> str:
    """Return the planUpdates rule string for the ask_trainer system prompt."""
    _intervals_rule = (
        "CRITICAL — intervals array: whenever the athlete changes interval count, duration, "
        'or power you MUST include the full "intervals" array in planUpdates. '
        "The array must contain EXACTLY the requested number of objects, one per interval rep. "
        'Each object: {"duration": <seconds>, "power": <watts>, "rest": <seconds>}. '
        "Example — athlete asks for 4×2 min at 370 W with 3 min rest: "
        '"intervals": ['
        '{"duration": 120, "power": 370, "rest": 180}, '
        '{"duration": 120, "power": 370, "rest": 180}, '
        '{"duration": 120, "power": 370, "rest": 180}, '
        '{"duration": 120, "power": 370, "rest": 180}]. '
        "Never describe the intervals only in text and omit the array — always materialise "
        "every rep as a separate object in the array."
    )
    _duration_rule = (
        "CRITICAL — duration: whenever you change how long a session is, you MUST set the "
        'numeric duration in planUpdates, not only in the "description" text. For a single '
        'target set "durationMinutes" (whole minutes); for a range set "durationMinMinutes" '
        'and "durationMaxMinutes" (e.g. a 3-4 h endurance ride is '
        '"durationMinMinutes": 180, "durationMaxMinutes": 240). '
        "Never state a new duration only in prose while leaving the number unchanged — the "
        "saved workout would still show the old length."
    )
    _rich_description_rule = (
        'Whenever you include a planUpdates entry, always include "workoutPurpose" '
        '(1-2 sentences on the physiological goal) and "keyFocusPoints" (array of 3-5 '
        "coaching-cue strings starting with an action verb). "
        'Also write "description" using the athlete\'s actual FTP/threshold HR to state '
        "exact power/HR targets — never write percentages alone."
    )
    _ride_label_rule = (
        '- "ride_label_update": an object to correct the displayed compliance badge for a '
        "completed activity. Use this when the athlete asks to fix, correct, or change the "
        "label shown on a past activity (e.g., 'Needs work', 'Too much'). "
        'Shape: {"activity_date": "YYYY-MM-DD", "label": "<new label>"}. '
        'Valid labels: "Done", "Partial", "Short", "Skipped", "Perfect", "Solid", "Close", '
        '"Off plan", "Needs work", "OK", "Recovery", "Warning", "Too much". '
        "Pick the label that best reflects what actually happened. "
        f"{BADGE_GROUNDING_RULE} "
        "Do not invent data-quality or missing-stream explanations unless the provided "
        "activity context explicitly says the data is missing or unreliable. "
        "CRITICAL — provisional classifications: an activity whose purpose is 'unknown' or whose "
        "classification confidence is low or medium (see the 'conf:' field and any "
        "'[classification: …]' note in the recent activity history) is a PROVISIONAL guess, not "
        "fact. Never tell the athlete they did a different session than they report — e.g. do not "
        "call a session a steady endurance ride when its classification is unknown/low-confidence. "
        "When the classification is provisional, say so and ask what they actually did rather than "
        "asserting the auto-detected type. If the athlete states what they did, trust their "
        "firsthand account over the provisional classification, acknowledge the detection was "
        "uncertain, and issue a ride_label_update reflecting the real session. Defend the stored "
        "classification only when its confidence is high."
    )
    _constraints_rule = (
        "CRITICAL — hard athlete constraints: before returning planUpdates, check the "
        "athlete profile, structured athlete context, evidence-backed memory facts, coach "
        "memory, and this conversation for availability constraints such as 'no training on "
        "Friday' or 'I have no time tomorrow'. Do not schedule workouts on constrained dates "
        "or weekdays, even if moving intensity there would be physiologically optimal. If a "
        "requested or recommended change conflicts with a constraint, keep that constrained "
        "day as rest/unavailable and choose the best available day, or ask one concise "
        "clarifying question if no feasible slot is clear. "
    )
    if context_workout:
        return (
            '- "planUpdates": an array of training day updates. '
            "Include this field in two cases: "
            "(1) The athlete explicitly asks to change, swap, skip, or reschedule any workout. "
            "(2) You propose a coaching change to the currently-viewed workout (see above) — "
            "even if the athlete did not explicitly request a change. "
            'Each update must include "date" (ISO string matching an existing plan date) and any '
            'fields to change: "workoutType", "title", "description", "durationMinutes", '
            '"targetPower", "targetHeartRate", "intervals", "workoutPurpose", "keyFocusPoints". '
            'Always include "title" and "description" so the plan entry stays informative. '
            'For a skipped/rest day set workoutType to "rest", durationMinutes to 0. '
            f"{_constraints_rule}"
            f"{_rich_description_rule} "
            f"{_intervals_rule} "
            f"{_ride_label_rule}"
        )
    return (
        '- "planUpdates": an array of training day updates (optional). Only include this '
        "field when the athlete explicitly asks to change, swap, skip, or reschedule a "
        'workout. Each update must include "date" (ISO string matching an existing plan '
        'date) and any fields to change: "workoutType", "title", "description", '
        '"durationMinutes", "targetPower", "targetHeartRate", "intervals", '
        '"workoutPurpose", "keyFocusPoints". '
        'Always include "title" and "description" so the plan entry stays informative. '
        'For a skipped/rest day set workoutType to "rest", durationMinutes to 0. '
        f"{_constraints_rule}"
        f"{_duration_rule} "
        f"{_rich_description_rule} "
        f"{_intervals_rule} "
        f"{_ride_label_rule}"
    )


def athlete_context_section(athlete_context: dict | None) -> str:
    if not athlete_context:
        return ""

    compact: dict[str, object] = {}
    for key, value in athlete_context.items():
        if value in (None, "", [], {}, "unknown"):
            continue
        compact[key] = value

    if not compact:
        return ""

    return (
        "\n\nStructured athlete context (durable coaching model): "
        f"{json.dumps(compact, ensure_ascii=False)}\n"
        "Use this as stable knowledge about how the athlete tends to train, "
        "respond to rest, stay motivated, and where coaching needs extra care. "
        "Do not repeat it verbatim; apply it only when relevant."
    )


def motivation_model_section(motivation: dict | None) -> str:
    """Render what the athlete is optimizing for (#562).

    This replaces ``athlete_context.motivation_drivers``, which reached the
    prompt as an unlabelled list of words the coach had to guess the meaning of.
    An objective says what the training is *for*, so it is stated as such — and
    kept deliberately compact, because the coach prompt is already ~16k tokens
    (#510/#556).

    The wording rules that make the coach *explain* itself in these terms are
    #565; this section only puts the objective in front of it.
    """
    if not motivation:
        return ""

    primary = str(motivation.get("primary_objective") or "").strip()
    secondary = [
        str(entry.get("text") or "").strip()
        for entry in motivation.get("secondary_objectives") or []
        if isinstance(entry, dict) and entry.get("status") == "active"
    ]
    constraints = [
        str(entry.get("text") or "").strip()
        for entry in motivation.get("constraints") or []
        if isinstance(entry, dict) and entry.get("status") == "active"
    ]
    secondary = [text for text in secondary if text]
    constraints = [text for text in constraints if text]

    if not (primary or secondary or constraints):
        return ""

    lines = ["\n\nWhat this athlete is training for (their own objective):"]
    if primary:
        # Confidence is surfaced so the coach can hold an inferred objective
        # loosely — and ask about it — rather than asserting it back at the
        # athlete as fact.
        confidence = motivation.get("primary_objective_confidence")
        inferred = motivation.get("primary_objective_source") != "user_set"
        qualifier = ""
        if inferred and isinstance(confidence, (int, float)):
            qualifier = f" (inferred, confidence {float(confidence):.2f})"
        lines.append(f"- Primary: {primary}{qualifier}")
    if secondary:
        lines.append(f"- Also matters: {'; '.join(secondary)}")
    if constraints:
        lines.append(f"- Must not be traded away: {'; '.join(constraints)}")
    lines.append(
        "Fitness is the means, not the end: this is what the training serves. "
        "Treat it as the athlete's goal even where it differs from what would "
        "maximise physiological adaptation."
    )
    return "\n".join(lines)


def athlete_model_section(athlete_model: dict | None) -> str:
    """Render the long-term athlete model (#384) for a coaching prompt.

    Only non-empty fields are included so a sparsely-derived model stays compact.
    ``confidence`` and ``updated_at`` are metadata and are not surfaced.
    """
    if not athlete_model:
        return ""

    # ``confidence``/``updated_at`` are coach/server metadata, not coaching
    # signal. Suppress both snake_case and camelCase spellings since callers may
    # pass either (the router serialises with ``by_alias=True``).
    metadata_keys = {"confidence", "updated_at", "updatedAt"}
    compact: dict[str, object] = {}
    for key, value in athlete_model.items():
        if key in metadata_keys:
            continue
        if value in (None, "", [], {}):
            continue
        compact[key] = value

    if not compact:
        return ""

    return (
        "\n\nLong-term athlete model (durable physiology & performance profile): "
        f"{json.dumps(compact, ensure_ascii=False)}\n"
        "These are slow-changing capabilities (threshold power, VO2 max, how the "
        "athlete holds threshold, recovers, and tolerates heat), not a report on a "
        "single session. Use them to set realistic targets and pacing; do not "
        "recite them verbatim."
    )


def athlete_performance_roi_section(recommendation: dict | None) -> str:
    """Render the ROI-based recommendation (#478) for the physiology layer.

    Turns the deterministic expected-gain-per-system map + weekly emphasis into a
    compact block the coach uses to back its physiology reasoning with the explicit
    performance model rather than ad-hoc periodization. Returns "" when the model
    has no confident limiter (``sufficient`` False) so the coach falls back to its
    own reasoning.
    """
    if not recommendation or not recommendation.get("sufficient"):
        return ""

    gains = "; ".join(
        f"{g.get('system')}: {g.get('gain')}"
        for g in recommendation.get("expected_gain", [])
        if g.get("system") and g.get("gain")
    )
    emphasis = "  ".join(
        f"{e.get('sessions')}× {e.get('label')}"
        for e in recommendation.get("weekly_emphasis", [])
        if e.get("label") and e.get("sessions")
    )

    lines = ["Performance-model ROI (deterministic, from the athlete's own numbers):"]
    hypothesis = recommendation.get("hypothesis")
    if hypothesis:
        lines.append(f"- Working hypothesis: {hypothesis}")
    if gains:
        lines.append(f"- Expected gain per system: {gains}")
    if emphasis:
        lines.append(f"- Suggested weekly emphasis: {emphasis}")
    rationale = recommendation.get("rationale")
    if rationale:
        lines.append(f"- Why: {rationale}")

    lines.extend(_utility_lines(recommendation.get("utility")))

    lines.append(
        "Use this as the physiology basis for which stimulus has the highest return "
        "now; explain the WHY in the athlete's terms rather than prescribing generic "
        "intervals. Recent load/freshness (CTL/ATL/TSB) and safety still gate whether "
        "today is the day for that stimulus."
    )
    return "\n".join(lines)


# How many ranked options reach the prompt. Enough to show that a trade-off was
# made and what lost, few enough to stay cheap in a ~16k-token prompt (#510/#556).
_UTILITY_OPTIONS_SHOWN = 4


def _utility_lines(utility: dict | None) -> list[str]:
    """The motivation-weighted ranking, rendered beside the physiology (#564).

    Both sub-scores are shown per option on purpose. The coach must be able to
    tell the athlete that the MTB session won on preference rather than on
    physiology — presenting a motivation-driven pick as the physiologically
    optimal one would be a lie the numbers right here contradict.
    """
    if not utility or not utility.get("options"):
        return []

    options = utility["options"][:_UTILITY_OPTIONS_SHOWN]
    ranked = "; ".join(
        f"{o.get('system')} @ {o.get('modality')} "
        f"(utility {o.get('utility')}, physiology {o.get('physiological_score')}, "
        f"motivation {o.get('motivation_score')})"
        for o in options
    )

    weights = utility.get("weights") or {}
    weight_text = ", ".join(f"{k} {v:.2f}" for k, v in weights.items() if v)

    lines = [
        "- Ranked by this athlete's own objective (utility = weighted physiology "
        f"+ motivation): {ranked}",
    ]
    if weight_text:
        lines.append(f"- Weights used: {weight_text}")

    excluded = utility.get("excluded") or []
    if excluded:
        removed = "; ".join(
            f"{o.get('system')} @ {o.get('modality')}" for o in excluded
        )
        lines.append(
            f"- Ruled out by the athlete's own constraints, not by physiology: {removed}"
        )

    lines.append(
        "When the top option by utility is not the top one by physiology, say so "
        "plainly — it won because it fits what this athlete trains for, and "
        "presenting it as the physiologically optimal choice would be dishonest."
    )
    return lines


def _attribute_summary(name: str, attr: dict) -> str | None:
    """One human line for a performance-model attribute, or None when empty."""
    if not isinstance(attr, dict):
        return None
    estimate = attr.get("estimate")
    score = attr.get("score")
    value = estimate if estimate not in (None, "") else score
    if value in (None, "", "unknown"):
        return None
    unit = attr.get("unit")
    label = name.replace("_", " ")
    rendered = f"{value}{f' {unit}' if unit else ''}"
    low = attr.get("estimate_low") or attr.get("estimateLow")
    high = attr.get("estimate_high") or attr.get("estimateHigh")
    if isinstance(low, (int, float)) and isinstance(high, (int, float)) and high > low:
        rendered += f" (plausible range {low:g}-{high:g}{f' {unit}' if unit else ''})"
    confidence = attr.get("confidence")
    conf_txt = f", confidence {float(confidence):.0%}" if isinstance(confidence, (int, float)) else ""
    line = f"- {label}: {rendered}{conf_txt}"
    missing = attr.get("missing_information") or attr.get("missingInformation")
    if missing:
        line += f" (still missing: {'; '.join(str(m) for m in missing)[:200]})"
    protocol = attr.get("validation_protocol") or attr.get("validationProtocol")
    if protocol:
        # An uncertain estimate ships with the test that would settle it, so the
        # coach can propose the test instead of asserting the number (#604).
        line += f"\n  - test that would settle it: {protocol}"
    return line


def performance_model_section(performance_model: dict | None) -> str:
    """Render the deterministic Athlete Performance Model + limiter (#476/#477).

    Gives the coach the drill-down substrate for Level-2 explainability (#480): the
    detected limiter with its confidence and the concrete evidence behind it, plus
    each inferred attribute with its own confidence and what is still missing. This
    is the coach's own inference from the athlete's numbers — every value carries a
    confidence and must never be presented as a measured fact.
    """
    if not performance_model:
        return ""

    attributes = performance_model.get("attributes") or {}
    attr_lines = [
        line
        for name, attr in attributes.items()
        if (line := _attribute_summary(name, attr)) is not None
    ]

    limiters = performance_model.get("limiters") or []
    limiter_lines: list[str] = []
    for cand in limiters[:2]:
        if not isinstance(cand, dict):
            continue
        name = cand.get("limiter")
        if not name:
            continue
        confidence = cand.get("confidence")
        conf_txt = (
            f" (confidence {float(confidence):.0%})"
            if isinstance(confidence, (int, float))
            else ""
        )
        evidence = cand.get("evidence") or []
        counter = cand.get("counter_evidence") or cand.get("counterEvidence") or []
        line = f"- {name.replace('_', ' ')}{conf_txt}"
        if evidence:
            line += f" — evidence: {'; '.join(str(e) for e in evidence)[:280]}"
        if counter:
            line += f" — but note: {'; '.join(str(c) for c in counter)[:200]}"
        limiter_lines.append(line)

    if not attr_lines and not limiter_lines:
        return ""

    parts = [
        "\n\nAthlete performance model (deterministic, inferred from the athlete's own "
        "rides — your COACH INFERENCE, not measured fact; every value carries a "
        "confidence):"
    ]
    if limiter_lines:
        parts.append("Most likely current limiter(s):")
        parts.extend(limiter_lines)
    if attr_lines:
        parts.append("Inferred attributes:")
        parts.extend(attr_lines)
    parts.append(
        "When the athlete asks WHY you recommend something, walk down from the claim "
        "to the limiter/attribute to this concrete evidence, and always state the "
        "confidence and what is still missing rather than asserting it as fact."
    )
    if any("test that would settle it" in line for line in attr_lines):
        parts.append(
            "An attribute that carries a range and a test is genuinely unsettled: "
            "give the range rather than the single number, say plainly that a hard "
            "interval proves a floor and not a threshold, and offer the test."
        )
    return "\n".join(parts)


def active_hypotheses_section(hypotheses: list[dict] | None) -> str:
    """Render the coach's active, testable hypotheses for the conversation (#479/#480).

    Surfaces each open hypothesis with its evidence, confidence and the competing
    explanations still to rule out, so the coach can defend a claim on demand and,
    when the data supports a materially better emphasis, offer it proactively as a
    hypothesis rather than a fact.
    """
    if not hypotheses:
        return ""

    entries: list[str] = []
    for hyp in hypotheses:
        statement = hyp.get("statement")
        if not statement:
            continue
        if hyp.get("status") not in (None, "proposed"):
            continue
        confidence = hyp.get("confidence")
        conf_txt = (
            f" (confidence {float(confidence):.0%})"
            if isinstance(confidence, (int, float))
            else ""
        )
        line = f"- {statement}{conf_txt}"
        evidence = hyp.get("evidence") or []
        if evidence:
            line += f"\n  Evidence: {'; '.join(str(e) for e in evidence)[:280]}"
        alternatives = (
            hyp.get("alternative_explanations")
            or hyp.get("alternativeExplanations")
            or []
        )
        if alternatives:
            line += (
                f"\n  Could also be: {'; '.join(str(a) for a in alternatives)[:280]}"
            )
        entries.append(line)

    if not entries:
        return ""

    return (
        "\n\nActive coaching hypotheses (tentative, testable ideas — NOT settled "
        "facts):\n"
        + "\n".join(entries)
        + "\nThese are working theories with explicit uncertainty. When one is "
        "relevant, you may raise it, but phrase it as a hypothesis and name its "
        "confidence and the alternative explanations. If asked to justify one, cite "
        "its evidence and say what would confirm or overturn it."
    )


def coach_explainability_rule() -> str:
    """Level-2 drill-down + proactive-insight rules for the coaching chat (#480)."""
    return (
        "\n\nExplainable coaching rules:\n"
        "- Any recommendation must be justifiable on demand. When the athlete asks "
        "'why?', 'what makes you say that?', or 'show me', walk down the chain: the "
        "recommendation → the model attribute or limiter it rests on → the concrete "
        "workouts, power/HR numbers and multi-week trend behind it → your confidence "
        "and what is still uncertain. Go one layer deeper each time they push.\n"
        "- Never present an inferred estimate (FTP, MAP, VO₂max, limiter, a "
        "hypothesis) as a measured fact. Attach the confidence and name the missing "
        "information in plain language; if a claim is a judgement call, say so.\n"
        "- Be proactive: when the performance model implies a materially higher-return "
        "training emphasis than the current plan is giving, surface it in one short, "
        "jargon-free sentence, phrased as a hypothesis, and offer to explain — e.g. "
        "'I think threshold work may buy you more right now than more VO₂max sessions "
        "— want me to explain why?'. Offer this at most once and never force it into "
        "an unrelated answer.\n"
        "- Prefer the athlete's own numbers and rides as evidence over generic "
        "sports-science; when you do lean on science, cite it in \"sources\" so the "
        "athlete can tell where a claim comes from."
    )


def objective_framing_rule() -> str:
    """Explain recommendations in the athlete's own objective, not in physiology (#565).

    The coach knows *why* a session works — that is what the performance model and
    the ROI recommendation are for. What it has been missing is what the session
    is **for**. "Threshold training increases FTP" is true and, for most athletes,
    beside the point: they do not want a bigger number, they want to still enjoy
    the last descent after four hours. Same prescription, and the difference
    between a coach that sounds like a spreadsheet and one that sounds like it
    knows them.

    Three things this rule has to get right, and each is a way it could go wrong:

    * **Chain, don't substitute.** The physiology stays; it becomes the middle of
      the sentence rather than the end of it.
    * **Never invent an objective.** An athlete with none inferred yet, or one
      held at low confidence, must not be told what they want. Falling back to
      plain physiology is the correct behaviour, not a failure.
    * **Stay honest about why an option won.** #564 can rank a lower-physiology
      option first because it fits the athlete better. Saying that plainly is
      the point; dressing it up as the physiologically optimal choice would make
      the whole feature a way of lying more fluently.
    """
    return (
        "\n\nExplain in the athlete's own objective (not in physiology):\n"
        "- The athlete's objective — what they actually train FOR — is given to "
        "you in the objective section when it is known. Physiology is the "
        "MECHANISM; their objective is the PAYOFF. End the chain on the payoff.\n"
        "- Concretely: instead of 'threshold training increases your FTP', say "
        "what the FTP buys them — e.g. 'this threshold session lets you climb "
        "faster with less fatigue, so you can still enjoy the final technical "
        "descent after four hours'. The prescription is identical; the reason "
        "you give for it is theirs, not the textbook's.\n"
        "- Restate goals in their terms too. A goal of 'increase FTP' is a means; "
        "the goal is what the FTP is for — 'climb faster between descents so one "
        "more trail fits into the ride'. When you name a target, name what it "
        "unlocks.\n"
        "- NEVER invent an objective. If no objective is given, or it is marked "
        "inferred with low confidence, explain in plain physiological terms and "
        "— at most occasionally — ask what they are training for. Telling an "
        "athlete what they want is worse than not knowing.\n"
        "- An inferred objective is held loosely: phrase it as your read of them "
        "('as I understand it, you ride mainly to…'), so they can correct it.\n"
        "- When the ranked options show a choice that won on the athlete's "
        "objective rather than on physiology, say so plainly — 'the MTB session "
        "is the slightly smaller training stimulus, but it is the one you will "
        "actually enjoy and finish'. Never present a preference-driven pick as "
        "the physiologically optimal one.\n"
        '- Record the objective link in the JSON field "objectiveRationale": one '
        "short phrase naming what this advice buys them in terms of their own "
        "objective. Leave it an empty string when no objective is known — do not "
        "fill it with a physiological restatement."
    )


def reveal_uncertainty_rule() -> str:
    """Make the coach own its uncertainty instead of arguing a single truth (#490).

    A recommendation is a judgement under incomplete data, not a verdict. When the
    call is genuinely uncertain — low/moderate confidence, or the athlete pushes back
    with their own signals — the coach must surface BOTH the evidence for and against
    its recommendation and name what it does not know, rather than defending the
    recommendation as the only correct answer.
    """
    return (
        "\n\nReveal your uncertainty (do not argue a single truth):\n"
        "- A recommendation is a judgement call under incomplete data, never a "
        "verdict. Own the uncertainty instead of defending the recommendation as the "
        "only correct answer.\n"
        "- When the call is genuinely uncertain — your confidence is low or moderate, "
        "the physiology and athlete-context layers diverge, or the athlete pushes back "
        "with their own signals (feels fresh, fast recovery, tolerated similar blocks "
        "before) — lay the uncertainty out honestly rather than restating the "
        "recommendation more firmly.\n"
        "- Show BOTH sides: name the evidence supporting your recommendation AND the "
        "evidence contradicting it (including what the athlete just told you), then say "
        "how confident you actually are and what you do not know (missing HRV, weak "
        "recovery model, uncertain MTB/other-sport power, etc.). Do not cherry-pick "
        "only the evidence that backs your call.\n"
        "- When the athlete asks to see the reasoning, you are unsure, or the two of "
        "you disagree, you may make this explicit with a short structure — the "
        "recommendation, your confidence (a rough %), a few supporting points, a few "
        "contradicting points, and the open unknowns — instead of a wall of prose. "
        "Keep each list to a few concrete bullets from THIS athlete's data.\n"
        "- If honest confidence is low and the contradicting evidence is real, say so "
        "and offer to let the athlete's judgement or a low-cost test decide, rather "
        "than insisting. Deferring under genuine uncertainty is correct coaching, not "
        "weakness.\n"
        "- When confidence is genuinely high and the evidence is one-sided, do not "
        "manufacture doubt or pad the answer — say it plainly and move on. Reserve the "
        "explicit breakdown for calls that are actually uncertain or contested."
    )


def recovery_framing_rule() -> str:
    """Explain a downgrade through who they are, not through what to avoid (#597).

    The coach's recovery reasoning is entirely risk management — keep fatigue
    low, protect the adaptation, stay fresh. Every clause is true and the whole
    thing reads as the absence of training, which is the wrong answer for an
    athlete whose goal is not a bigger number.

    #565 already says explain in the athlete's objective. It loses here, because
    the rules it competes with (:func:`rest_recommendation_rules`) are physiology
    end to end and there was nothing in the prompt to explain *this* athlete's
    easy day with. :mod:`services.rider_identity` supplies that; this says what
    to do with it.

    On the banned vocabulary: the issue lists bare words — "don't", "avoid",
    "protect". Banning those as words would mangle ordinary English and the model
    would rightly ignore it, so what is named here is the *phrasing*, literally
    and in full. That is the #593 lesson: "avoid generic framing" is advice a
    model agrees with and then does not follow; a list of sentences is not.
    """
    return (
        "\n\nFraming a recommendation to do less:\n"
        "- This applies whenever you recommend LESS than the athlete could "
        "physically do — a rest day, a recovery spin, yoga or mobility instead of "
        "a ride, a shorter or easier version of what was planned.\n"
        "- Chain the reason: what you know about them as a rider → the pattern "
        "that makes this the right call for them → what it buys their own "
        "objective. Physiology stays, as the middle of that chain and never as "
        "the end of it. The 'Who this athlete is as a rider' section gives you "
        "the first two when they are known.\n"
        "- Name the pattern as a fact about how they ride, never as a failing. "
        "'The danger is the moment a target appears and the ride becomes a "
        "pursuit — that is a behavioural pattern, not a discipline problem' is "
        "the register. Never imply weak willpower, and never congratulate them "
        "for resisting themselves.\n"
        "- Say what the easier choice BUYS, not what it prevents. These framings "
        "are banned: 'protect your recovery', 'keep your fatigue low', 'keep "
        "systemic load flat', 'avoid digging into your reserves', 'don't overdo "
        "it', and calling it 'skipping training' or 'a day off'. Use instead: it "
        "invests in how the next key ride feels, it preserves the quality of the "
        "riding they care about, it means arriving fresher for the day that "
        "matters, it improves how the body feels on the trail.\n"
        "- NEVER invent an identity. With no rider section given, or a pattern "
        "held below the confidence stated there, explain in plain terms and do "
        "not attribute a character to them. Telling an athlete who they are is "
        "worse than not knowing — the same rule as for their objective.\n"
        "- One or two sentences of this, woven into the answer. It is a reason, "
        "not a section: never label it, never list the steps, and do not repeat "
        "the pattern back at them every time it applies."
    )


def rider_identity_section(identity: dict | None) -> str:
    """Who this athlete is as a rider, when enough has been observed (#597).

    Volatile — it is a picture that grows — so it sits with the athlete data
    rather than in the cached prefix. Absent for a new athlete: a pattern has to
    be seen twice, in separate messages, before the coach may build a sentence
    about someone's character on it.
    """
    if not identity:
        return ""

    lines = ["\n\nWho this athlete is as a rider (observed, not assumed):"]
    style = identity.get("style")
    if isinstance(style, dict) and style.get("reading"):
        lines.append(
            f"- Style: {style['reading']} — read from {style.get('basis', '')} "
            f"(confidence {float(style.get('confidence') or 0):.2f})"
        )

    patterns = identity.get("patterns") or []
    for entry in patterns:
        if not isinstance(entry, dict) or not entry.get("pattern"):
            continue
        lines.append(
            f"- Pattern: {entry['pattern']} "
            f"(confidence {float(entry.get('confidence') or 0):.2f}). "
            f"On a session meant to be easy, {entry.get('onAnEasyDay', '')}"
        )
        words = entry.get("theirWords")
        if words:
            lines.append(f"  Their words: \"{words}\"")

    if len(lines) == 1:
        return ""
    lines.append(
        "Use this to explain a recommendation to do less, not to label them. "
        "State a pattern as how they ride, never as something to overcome, and "
        "hold it at the confidence given — these are readings they can correct."
    )
    return "\n".join(lines)


def curiosity_rule() -> str:
    """Be interested in the ride, not only in the numbers from it (#593).

    The old shape was validate → advise conservatively → ask how the legs feel.
    Correct, and worth almost nothing: it spent the one follow-up question a
    reply is allowed on the least informative thing available, while the rider
    the athlete chased, the pacing they chose and the week they spent ill went
    unremarked.

    Static, so it belongs to the cacheable prefix and names the section it
    depends on rather than pointing at a position (#514). Which signal is
    interesting is decided in Python — :mod:`services.workout_curiosity` — and
    arrives in that section. This is only the shape and the prohibition.
    """
    return (
        "\n\nCuriosity rules (post-workout messages):\n"
        "- These three questions are banned: 'how do your legs feel', 'how is your "
        "recovery', 'how are you feeling now'. Ask one only when you genuinely "
        "cannot advise without the answer, and then say why you need it. Once the "
        "athlete has told you a story, asking one of these says you read the watt "
        "numbers and skipped the sentence.\n"
        "- When the 'Worth being curious about' section names a signal, that signal "
        "is your follow-up question. Ask it in your own words, about this specific "
        "session — never a stored phrasing, never a generic version of it.\n"
        "- With that section present, shape the response in four short parts, "
        "unlabelled and written as prose: (1) what the numbers say, briefly; "
        "(2) what is unusual or interesting about them; (3) what that suggests "
        "about them as a rider, offered as a reading they can correct and not as a "
        "verdict; (4) the one curious question.\n"
        "- Part (3) is a hypothesis about a person, so hedge it honestly ('this "
        "reads more like…', 'if that holds, then…') and drop it entirely when the "
        "evidence is one session.\n"
        "- Insight, not entertainment. Do not become chatty, jokey or effusive to "
        "seem interested — a real coach leaning forward asks a sharper question, "
        "they do not talk more.\n"
        "- Without that section, do not manufacture curiosity. A message with "
        "numbers and no story is answered as what it is."
    )


def workout_curiosity_section(curiosity: dict | None) -> str:
    """What the deterministic pass found worth noticing in this message (#593).

    Volatile by construction — it is about the sentence the athlete just typed —
    so it sits with the athlete data rather than in the cached prefix.

    It carries a topic and never a question. A stored question would be read out
    verbatim and every athlete would get the same sentence, which is the failure
    this issue is about repeating itself one level up.
    """
    if not curiosity:
        return ""

    noticed = curiosity.get("noticed") or []
    compact = [
        f"{item.get('kind')}: {item.get('signal')}"
        for item in noticed
        if item.get("signal")
    ]
    if not compact:
        return ""

    lines = [
        "\n\nWorth being curious about (read from this athlete's own message):",
        "What stood out:",
        *(f"- {entry}" for entry in compact),
    ]
    curious_about = curiosity.get("curiousAbout") or curiosity.get("curious_about")
    if curious_about:
        lines.append(f"Ask about: {curious_about}")
    why = curiosity.get("whyItMatters") or curiosity.get("why_it_matters")
    if why:
        lines.append(f"Why it is worth a question: {why}")
    reading = curiosity.get("readingToOffer") or curiosity.get("reading_to_offer")
    if reading:
        lines.append(
            f"A reading you may offer, hedged and correctable: {reading}"
        )
    words = curiosity.get("theirWords") or curiosity.get("their_words")
    if words:
        lines.append(f"Their words: \"{words}\"")
    lines.append(
        "Use the physiological items above for the analysis, not for the question — "
        "the training data already answers those. Ask about the one named."
    )
    return "\n".join(lines)


def weather_scheduling_rule() -> str:
    """Let the forecast move sessions — but only as far as this athlete warrants (#495).

    An upcoming forecast is a scheduling constraint, not a mandate: 40 °C on the day
    of a VO2max block is a real reason to move it, while a warm day for a rider whose
    own history shows they hold power in heat is not. The learned tolerances arrive in
    the prompt as confidence-scored beliefs, so this rule ties the strength of the
    intervention to the strength of the belief and requires the coach to say when
    weather is the reason a session changed.
    """
    return (
        "\n\nWeather-aware scheduling rules:\n"
        "- Treat the upcoming forecast as a scheduling constraint on sessions that have "
        "not happened yet. Adapt the plan, never silently rewrite it: prefer moving or "
        "softening one session over reshuffling the whole week.\n"
        "- Concrete moves worth making: shift high-intensity work off extreme-heat days "
        "or offer a cooler early-morning window, offer an indoor trainer session in "
        "severe rain, snow, thunderstorms, or high wind, swap to endurance, recovery, or "
        "strength work when outdoor intensity is unsafe, and extend warmups in the cold.\n"
        "- Condition every weather call on this athlete's learned tolerances. Do not "
        "move a session for conditions they demonstrably handle well, and weight the "
        "advice by the confidence attached to that belief — a low-confidence tolerance "
        "is a reason to ask, not to assume.\n"
        "- When the athlete tells you how they feel about a condition ('I actually love "
        "the rain', 'heat kills me'), treat that as direct evidence about them that "
        "outranks the population average, and reflect it in what you advise next.\n"
        "- Whenever weather is why you moved, softened, or kept a session, say so "
        "explicitly and name the day and the condition. A weather-driven change the "
        "athlete cannot trace back to a reason reads as the plan churning by itself."
    )


def update_model_before_plan_rule() -> str:
    """Update the belief (athlete model) before touching the decision (plan) (#491).

    New evidence must first change the coach's internal understanding — the athlete
    model, its hypotheses, and its confidence — and only THEN drive a plan decision.
    Treating the plan as the primary state produces oscillating, contradictory changes
    across a conversation. A belief update does NOT imply a plan change: keeping the
    current plan while confidence has dropped is a valid, transparent outcome.
    """
    return (
        "\n\nUpdate your understanding before the plan (belief first, then decision):\n"
        "- The training plan is NOT your primary state — your model of the athlete is. "
        "When new evidence arrives (the athlete pushes back, reports how they feel, "
        "corrects the record, or new data lands), update your understanding FIRST, then "
        "decide about the plan. Do not reach for a plan edit as the immediate reflex to "
        "new information.\n"
        "- Follow this order: new evidence → update the athlete model (what you now "
        "believe and how the hypotheses shift) → re-estimate confidence → only then "
        "decide whether the current plan is still the highest-value choice. Skipping "
        "straight to a plan change is what makes recommendations oscillate — a later "
        "question flips the plan back with no explanation.\n"
        "- A belief update does NOT imply a plan change. Lower confidence in a "
        "hypothesis (e.g. the recovery/intensity-creep model weakens because MTB power "
        "looks unreliable or a ride's classification was low-confidence) often leaves "
        "the best plan unchanged. 'No change' is a valid, correct outcome — say so and "
        "explain why the expected value still favours the current plan.\n"
        "- When your understanding shifts, make the belief update explicit BEFORE any "
        "plan talk: name what changed (which hypothesis, roughly from what confidence to "
        "what, and why). Then give the plan decision separately — change or no change — "
        "with its own reason tied to expected value, not merely repeating the previous "
        "advice.\n"
        "- Only emit planUpdates once the decision genuinely follows from the updated "
        "model. If the model moved but the plan should not, keep planUpdates empty and "
        "state that the plan stands despite the revised understanding. Never let a "
        "follow-up question silently reverse an earlier plan change without first "
        "revisiting what you believe and why."
    )


def open_questions_section(open_questions: list[dict] | None) -> str:
    """Render the coach's still-open questions (#385) for a coaching prompt.

    Surfaces only ``open`` questions so the coach knows what it is still trying
    to figure out and can seize a chance to answer one, without treating the
    uncertainty as settled fact. Resolved questions are omitted.
    """
    if not open_questions:
        return ""

    compact_questions: list[dict[str, object]] = []
    for question in open_questions:
        if question.get("status") not in (None, "open"):
            continue
        text = question.get("question")
        if not text:
            continue
        compact: dict[str, object] = {"question": text}
        evidence = question.get("evidence")
        if evidence:
            compact["evidence"] = str(evidence)[:240]
        needs = question.get("needs")
        if needs:
            compact["needs"] = str(needs)[:240]
        compact_questions.append(compact)

    if not compact_questions:
        return ""

    return (
        "\n\nOpen questions the coach is still trying to answer about this athlete: "
        f"{json.dumps(compact_questions, ensure_ascii=False)}\n"
        "These are acknowledged uncertainties, not facts. Do not assert them as "
        "settled. When the conversation or recent data offers a chance to resolve "
        "one — or a low-cost test in 'needs' fits naturally — take it, but never "
        "force it into an unrelated answer."
    )


def pending_inquiries_section(inquiries: list[dict] | None) -> str:
    """Render the questions already pinned to the athlete in the chat (#506).

    The pin owns these questions. Without this block the coach re-asks them in
    prose — the athlete gets the same question twice in one screen, once pinned
    and once conversationally, and answers it in the reply box where the
    inquiry's answer check never sees it.
    """
    if not inquiries:
        return ""

    compact: list[dict[str, object]] = []
    for inquiry in inquiries:
        if inquiry.get("status") not in (None, "pending"):
            continue
        question = inquiry.get("question")
        if not question:
            continue
        entry: dict[str, object] = {"question": str(question)[:240]}
        why = inquiry.get("whyAsking") or inquiry.get("why_asking")
        if why:
            entry["whyAsking"] = str(why)[:240]
        compact.append(entry)

    if not compact:
        return ""

    return (
        "\n\nQuestions you have already put to this athlete and are waiting on: "
        f"{json.dumps(compact, ensure_ascii=False)}\n"
        "They are pinned in the chat with their own answer box, so do NOT ask them "
        "again here — asking twice reads as not listening. If the athlete answers "
        "one of them in passing, simply use what they said."
    )


def athlete_memory_facts_section(facts: list[dict] | None) -> str:
    if not facts:
        return ""

    compact_facts: list[dict[str, object]] = []
    for fact in facts:
        status = fact.get("status")
        confidence = float(fact.get("confidence") or 0)
        if status in ("rejected", "stale", "needs_validation"):
            continue
        if status != "user_confirmed" and confidence < 0.5:
            continue
        compact: dict[str, object] = {
            "category": fact.get("category"),
            "fact": fact.get("fact"),
            "confidence": round(confidence, 2),
            "status": status,
        }
        source = fact.get("sourceSnippet") or fact.get("source_snippet")
        if source:
            compact["evidence"] = str(source)[:240]
        last_confirmed = fact.get("lastConfirmedAt") or fact.get("last_confirmed_at")
        if last_confirmed:
            compact["lastConfirmedAt"] = str(last_confirmed)
        observations = fact.get("observationCount") or fact.get("observation_count")
        if observations:
            compact["observationCount"] = observations
        # Default to the safer "observation" classification for legacy rows that
        # predate the kind discriminator (#386).
        kind = "fact" if fact.get("kind") == "fact" else "observation"
        compact_facts.append((kind, compact))

    if not compact_facts:
        return ""

    stable_facts = [c for kind, c in compact_facts if kind == "fact"]
    observations = [c for kind, c in compact_facts if kind == "observation"]

    sections: list[str] = []
    if stable_facts:
        sections.append(
            "Stable athlete facts (measured/stated values — FTP, max HR, weight): "
            f"{json.dumps(stable_facts, ensure_ascii=False)}"
        )
    if observations:
        sections.append(
            "Behavioural observations (patterns inferred from training history — treat "
            "as tendencies, not certainties): "
            f"{json.dumps(observations, ensure_ascii=False)}"
        )

    return (
        "\n\nEvidence-backed athlete memory (durable, vetted):\n"
        + "\n".join(sections)
        + "\nUse these only when relevant. Treat confidence, evidence, and freshness "
        "as part of each item; never infer stronger claims than it supports. Weight "
        "recommendations toward higher-confidence items and lean on stable facts more "
        "firmly than on inferred observations; verify lower-confidence items with a "
        "short question rather than acting on them as settled."
    )


def recommendation_reasoning_layers_rule() -> str:
    return (
        "\n\nRecommendation reasoning layers:\n"
        "- Separate the decision into two internal layers before recommending a workout.\n"
        "- Physiology layer: CTL, ATL, TSB, HRV/sleep if provided, recent load, subjective "
        "fatigue, and the planned training stimulus.\n"
        "- When a performance-model ROI block is present, let it drive which stimulus has "
        "the highest expected return (its detected limiter and expected-gain-per-system), "
        "rather than defaulting to generic periodization; still gate the timing on load and "
        "freshness. When it is absent or low-confidence, reason from the numbers as usual.\n"
        "- Athlete-context layer: motivation, rest tolerance, tendency to overdo it, social "
        "needs, mood, adherence pattern, structured athlete context, and evidence-backed "
        "memory facts.\n"
        "- If two options are physiologically similar, choose the one that better fits the "
        "athlete-context layer, such as an easy/social ride for motivation or recovery when "
        "the athlete tends to overdo it.\n"
        "- If the physiology layer and athlete-context layer leave two meaningfully different "
        "recommendations plausible, ask exactly one short targeted learning question before "
        "committing. Use this only when the answer would materially change the recommendation.\n"
        "- Good targeted questions distinguish the missing context, e.g. 'Do you need training "
        "stimulus today, or mainly head-clearing?', 'Are you restless because you feel fresh, "
        "or because you are worried about losing fitness?', or 'Would a social ride help you "
        "more than another structured session today?'\n"
        "- Do not over-ask. If recent data, athlete context, or safety/fatigue signals already "
        "make the recommendation clear, give the recommendation directly.\n"
        "- These two layers are distinct knowledge sources: the physiology layer is your "
        "COACH INFERENCE from the athlete's numbers, and the athlete-context layer is a "
        "PERSONAL OBSERVATION about this specific athlete. Any sports-science you rely on is "
        'a third source — cite it in "sources" — so the athlete can always tell where a '
        "claim comes from.\n"
        "- Always record your two-layer reasoning in the JSON fields "
        '"physiologyRationale" (one short phrase on what the numbers/load/freshness alone '
        "suggest — your coach inference) and \"contextRationale\" (one short phrase on what "
        "personal context — rest response, motivation, overtraining tendency, social needs, "
        "preferences — suggests, i.e. a personal observation). "
        "Leave a field as an empty string only when that layer genuinely adds nothing.\n"
        "- When the two layers AGREE, keep the final response concise and natural and do not "
        "expose the layer labels.\n"
        "- When the two layers DIVERGE — the numbers alone would point one way but knowing this "
        "athlete you would advise differently — make that transparent in a natural voice, e.g. "
        "'On the numbers an easy Z2 ride is fine today, but knowing how you tend to turn easy "
        "rides into hard ones, I'd take the full rest day.' Name that it is a personal-context "
        "call, not a numbers call, without using jargon or the internal layer labels.\n"
        "- Keep this to one or two sentences. Never turn the response into a verbose breakdown "
        "of every metric."
    )


def rest_recommendation_rules() -> str:
    return (
        "\n\nRest/recovery recommendation rules:\n"
        "- When the athlete challenges a rest day or asks whether an easy ride is possible, "
        "make the rest decision from the numbers and context: CTL, ATL, TSB, recent TSS, "
        "RPE, subjective leg feel, sleep/HRV if provided, recent density, and availability constraints.\n"
        "- After a hard VO2max or threshold session, require at least one easy/recovery day. "
        "An unavailable day with no training counts as that recovery day.\n"
        "- Do not claim two complete rest days are mandatory unless the data supports it: "
        "examples include strongly negative TSB, ATL clearly above CTL, very high RPE, heavy legs, "
        "poor sleep/HRV, illness, pain, or a dense recent load block.\n"
        "- With neutral or positive TSB, ATL near or below CTL, and good subjective feedback "
        "(e.g. fresh legs), a bounded easy Z2/recovery ride after one full rest day is usually "
        "acceptable unless a hard constraint or clear fatigue signal says otherwise.\n"
        "- If the athlete corrected the chronology or availability (for example, the hard workout "
        "was moved earlier because tomorrow is unavailable), re-evaluate from that corrected "
        "sequence instead of repeating the previous plan logic.\n"
        "- When the athlete asks to show the numbers, explicitly state what each number supports "
        "and what it does not support; avoid generic supercompensation or overtraining language "
        "unless it is tied to concrete evidence.\n"
        "- If allowing easy endurance before an upcoming intensity day, set clear limits "
        "(duration, Z2/recovery intensity, no surges) and make the next hard session conditional "
        "on morning freshness.\n"
        "- These rules decide WHETHER to recommend rest. They do not decide how to "
        "say it: once you have made the call, the framing rules for recommending "
        "less than the athlete could do apply, and they outrank the risk-management "
        "vocabulary here."
    )


def coach_static_prefix() -> str:
    """The cacheable head of the coach system prompt (#514, #538).

    Byte-identical for every athlete, every day and every combination of
    optional sections — that is the whole point, and
    ``test_coach_prompt_cache.py`` holds it to that. It is a named function so
    the property is testable and so ``scripts/probe_implicit_cache.py`` can
    measure the real prefix rather than a stand-in.

    Nothing conditional belongs in here: a rule that appears only sometimes
    breaks the prefix for the whole request. Volatile athlete data and the
    output contract follow it in :func:`ask_trainer_system`.
    """
    # Proactive solicitation and structured feedback extraction instructions
    feedback_instructions = (
        "\n\nActivity feedback rules:\n"
        "- If any activity in the recent activity history (last 3 days) has no user note, "
        "proactively ask the athlete how it felt — briefly and naturally woven into your response.\n"
        "- When the athlete describes how a specific activity felt, populate "
        '"ride_note_update": {"activity_date": "YYYY-MM-DD", "note": "1-2 sentence summary"} '
        'in your JSON response. Omit "ride_note_update" entirely when no activity is being described.'
    )

    recommendation_layers_instructions = recommendation_reasoning_layers_rule()
    explainability_instructions = coach_explainability_rule()
    objective_instructions = objective_framing_rule()
    uncertainty_instructions = reveal_uncertainty_rule()
    model_before_plan_instructions = update_model_before_plan_rule()
    rest_instructions = rest_recommendation_rules()
    hard_spacing_instructions = hard_session_spacing_rules()

    attentive_coach_instructions = (
        "\n\nAttentive coach rules:\n"
        "- Listen closely to what the athlete actually says, including casual or messy messages. "
        "Treat high-signal details as coaching data, not small talk.\n"
        "- High-signal details include: long duration, back-to-back training days, unusually hot or cold "
        "weather, running out of drink or food, cramping, bonking, dizziness, illness, pain, unusually high "
        "fatigue, poor sleep, or a ride that was much harder/easier than planned.\n"
        "- When a high-signal detail is important and one piece of information is missing, ask one concise "
        "targeted follow-up question before giving overly confident advice. It is okay for that one question "
        "to combine two tightly related details, e.g. 'Wie viel und was hast du unterwegs getrunken?' for "
        "a long hot ride where the athlete ran out of drink.\n"
        "- Do not interrogate every minor detail. Skip follow-up questions when the detail is trivial, already "
        "clear enough, or would not change the coaching recommendation.\n"
        "- When the athlete replies with important specifics such as hydration volume, drink type, sodium/carbs, "
        "fueling, heat tolerance, symptoms, or recurring fatigue, treat that as information worth preserving "
        "through coach memory when it can affect future training.\n"
        "- Reply in the same language the athlete used unless they ask otherwise."
    )

    curiosity_instructions = curiosity_rule()
    recovery_framing_instructions = recovery_framing_rule()

    # Outlook instructions: guide the coach when the athlete asks for a session preview
    outlook_instructions = (
        "\n\nOutlook rules:\n"
        "- When the athlete asks for an outlook on upcoming sessions (e.g. 'show outlook', "
        "'what are my next sessions', 'what\\'s coming up', 'preview my training', "
        "'walk me through upcoming training'), give a natural, readable explanation of the "
        "next 3-5 scheduled sessions: what each session involves, why they are ordered that "
        "way, and how the sequence fits the athlete\\'s current fatigue and readiness. "
        "Draw on current CTL/ATL/TSB (or recent training history) and any recent ride "
        "feedback to contextualize the upcoming load.\n"
        "- Interpret 'upcoming', 'next', and 'coming days' as TODAY and future dates only. "
        "Never answer those questions from Last 7 days entries or older conversation history.\n"
        "- If the athlete asks about the next N days, use exactly the first N entries from "
        "Upcoming plan unless they explicitly ask to exclude today.\n"
        "- If the athlete asks whether upcoming days are all rest, verify each relevant Upcoming "
        "plan entry's workoutType/title. Do not call recovery spins, strength sessions, or other "
        "non-rest workouts 'pure rest'.\n"
        "- When giving an outlook, do NOT include planUpdates unless the athlete explicitly "
        "asks to change something or you detect a clear recovery issue that requires "
        "immediate intervention (e.g. dangerously high accumulated fatigue heading into "
        "a hard block)."
    )
    constraint_instructions = (
        "\n\nHard constraint rules:\n"
        "- Treat athlete availability constraints from the profile, structured athlete context, "
        "evidence-backed memory facts, coach memory, or this conversation as binding. "
        "Examples: no time Friday, travel day, work commitment, family obligation, no training tomorrow.\n"
        "- Do not move workouts onto constrained dates or weekdays, even when that would be the "
        "physiologically optimal placement.\n"
        "- Before returning planUpdates, explicitly check that every updated training day still "
        "respects those constraints. If there is no feasible slot, ask one concise clarifying "
        "question instead of silently violating the constraint."
    )

    # --- Cache-friendly assembly (#514) ---------------------------------------
    # Gemini's implicit caching bills a repeated *prefix* at 10 % of the input
    # rate, but only matches from the very first token.  This prompt used to open
    # with today's date and close with ~6,900 tokens of fixed rules, so the prefix
    # changed on every single turn and the cache could never hit.  The rules are
    # identical for every athlete on every day, so they go first and the volatile
    # athlete data goes after them.  Google's own guidance is the same: put large
    # and common contents at the beginning.
    #
    # Anything conditional is kept *out* of this block — a rule that appears only
    # sometimes would break the prefix for the whole request, which is why the
    # weather rules, the output contract and the reasoning framework sit in the
    # closing block below instead.
    static_instructions = (
        f"{COACH_PERSONA} Answer the athlete's question concisely and practically."
        f"{feedback_instructions}"
        f"{recommendation_layers_instructions}"
        f"{explainability_instructions}"
        f"{objective_instructions}"
        f"{uncertainty_instructions}"
        f"{model_before_plan_instructions}"
        f"{rest_instructions}"
        f"{hard_spacing_instructions}"
        f"{attentive_coach_instructions}"
        f"{curiosity_instructions}"
        f"{recovery_framing_instructions}"
        f"{constraint_instructions}"
        f"{outlook_instructions}\n\n"
        "Always take today's date into account when answering — for example when calculating "
        "days until a race, suggesting which workout is next, or referencing past sessions.\n"
        "Date awareness rules:\n"
        # Named, not positional: this block is now read before the data it points
        # at, and a rule that says "above" would be pointing at nothing (#514).
        "- Treat the Current local date context section as authoritative, regardless of model "
        "knowledge or conversation history.\n"
        "- When using words like today, tomorrow, or yesterday, anchor them to the exact dates "
        "listed there.\n"
        "- If you name a weekday for today, tomorrow, or yesterday, copy it from that date context; "
        "do not infer or recalculate it.\n"
        "- If you name a weekday for an upcoming plan day, copy the plan entry's weekday/dateLabel "
        "fields. Never invent a weekday from memory.\n"
        "- Before returning, check every weekday/date pair in the response against the Current local "
        "date context and the Upcoming plan dateLabel fields. If a pair conflicts, fix it before "
        "answering.\n"
        "- If the athlete states a relative date that conflicts with the date context, gently "
        "clarify using the exact date.\n"
        "Whenever the athlete requests a change to the training plan, your response MUST briefly "
        "reflect on whether the change is a good idea: acknowledge their preference warmly, give "
        "an honest assessment of the training impact (e.g. how it affects load, intensity, "
        "progression, or recovery), and explain any trade-offs. "
        "Always be kind, supportive, and encouraging — but never withhold honest coaching "
        "advice. If a change could harm progress or recovery, say so clearly yet tactfully, "
        "and still apply the change if the athlete wants it.\n"
        "Response quality rules (apply to the 'response' field):\n"
        "- Do NOT address the athlete by name in most messages. Using someone's name repeatedly "
        "feels unnatural — a real coach does not say 'Good point, Alex' or 'I get that, Alex' in "
        "every other sentence. At most use the name once every several exchanges, and never at the "
        "start of a sentence as a filler (e.g. NEVER 'That works, Jürgen.' or 'I get why, Jürgen,').\n"
        "- Short fillers are okay occasionally (e.g. 'Great question'), but they must not appear as "
        "a default opener. Vary openings so responses do not sound templated or repetitive.\n"
        "- Avoid repeatedly starting with the same recent-ride recap phrasing (e.g. 'You did a tough "
        "ride yesterday...') unless that specific detail is the most helpful way to answer this "
        "question.\n"
        "- Acknowledge uncertainty when the data is sparse or weak — never over-interpret limited "
        "information; instead, ask one concise question to fill the gap.\n"
        "- Reference at least one concrete detail specific to this athlete or their recent rides "
        "(a power number, a duration, a comment they made) so the reply feels personal, not generic.\n"
        "- Give one clear next action or coaching recommendation so the athlete always knows what "
        "to do with your answer.\n"
        "- Ask at most one follow-up question per response — never stack multiple questions.\n"
        "- When an athlete asks for an outlook, give a warm narrative of their next 3-5 sessions: "
        "what each involves, why they are ordered that way, and how the block fits their current "
        "fatigue — then stop; do not modify the plan unless explicitly asked.\n\n"
        "Example of a well-formed 'response' field (athlete asking for an outlook):\n"
        "  response: \"Coming off yesterday's threshold work, tomorrow is a 45-minute recovery spin "
        "to let the adaptation settle. Saturday is your long endurance ride — 2.5 hours in Z2, "
        "which is the cornerstone of your base block. Sunday is rest. That sequence gives you "
        "quality stress followed by two easier days, which is exactly right given your TSB is "
        'currently sitting around −15. Any of those sessions you want to talk through?"'
    )
    return static_instructions


def ask_trainer_system_sections(
    profile: dict,
    today: str,
    last_7_days: list[dict],
    next_n_days: list[dict],
    assessment_section: str,
    memory_section: str,
    workout_section: str,
    plan_updates_rule: str,
    athlete_context: dict | None = None,
    athlete_memory_facts: list[dict] | None = None,
    athlete_model: dict | None = None,
    motivation_model: dict | None = None,
    open_questions: list[dict] | None = None,
    pending_inquiries: list[dict] | None = None,
    performance_model: dict | None = None,
    performance_recommendation: dict | None = None,
    hypotheses: list[dict] | None = None,
    science_context: str = "",
    training_load: dict | None = None,
    classification: dict | None = None,
    metrics_history_section: str = "",
    race_events_section: str = "",
    weather_context_section: str = "",
    training_status_badge: tuple[str | None, str | None, str | None] | None = None,
    date_context: str = "",
    workout_curiosity: dict | None = None,
    rider_identity: dict | None = None,
) -> dict[str, str]:
    """The coach system prompt as named parts, in the order they are sent.

    Split out so the prompt can be *measured* — where its bulk sits has been
    guessed at twice and gone stale both times (#556). Joining the values is
    :func:`ask_trainer_system` and produces exactly the string it always did.
    """
    science_section = (
        (
            f"\n\nRelevant cycling science research (use this to ground your advice in evidence):\n"
            f"{science_context}"
            "\nWhen citing these sources, include the title in your response."
        )
        if science_context
        else ""
    )

    training_load_section = ""
    if training_load and not metrics_history_section:
        # Fall back to plan-derived CTL/ATL/TSB only when no actual-ride metrics are available
        training_load_section = (
            f"\n\nCurrent training load (estimated from plan): "
            f"CTL (fitness)={training_load.get('ctl')} "
            f"ATL (fatigue)={training_load.get('atl')} "
            f"TSB (form)={training_load.get('tsb')}\n"
            "Use TSB to guide your advice: TSB < −20 suggests accumulated fatigue, prioritise recovery; "
            "TSB > +10 before a key workout suggests freshness, intensity can be increased."
        )

    metrics_section = (
        f"\n\n{metrics_history_section}" if metrics_history_section else ""
    )
    events_section = f"\n\n{race_events_section}" if race_events_section else ""
    # The upcoming per-day outlook plus what has been learned about this athlete's
    # own weather tolerances (#495) — the coach may use it to move or soften a
    # session, but only where this athlete's own history says the weather matters.
    weather_section = (
        f"\n\n{weather_context_section}" if weather_context_section else ""
    )
    # The athlete can see this badge on their dashboard and will ask about it by
    # its exact words, so the coach has to be holding the same label it wrote.
    status_badge_section = training_status_section(*(training_status_badge or (None, None, None)))
    durable_context_section = athlete_context_section(athlete_context)
    durable_motivation_section = motivation_model_section(motivation_model)
    durable_model_section = athlete_model_section(athlete_model)
    durable_memory_facts_section = athlete_memory_facts_section(athlete_memory_facts)
    durable_open_questions_section = open_questions_section(open_questions)
    pinned_inquiries_section = pending_inquiries_section(pending_inquiries)
    perf_model_section = performance_model_section(performance_model)
    roi_section = athlete_performance_roi_section(performance_recommendation)
    roi_section = f"\n\n{roi_section}" if roi_section else ""
    hypotheses_section = active_hypotheses_section(hypotheses)
    race_profile_section = race_profile_context_section(profile)
    race_profile_section = (
        f"\n\n{race_profile_section}\n" if race_profile_section else ""
    )

    classification_section = ""
    if classification:
        classification_section = (
            f"\n\nQuestion classification: category={classification.get('category')} "
            f"needs_science_rag={classification.get('needs_science_rag')}"
        )

    weather_instructions = weather_scheduling_rule() if weather_context_section else ""

    static_instructions = coach_static_prefix()

    # Everything below changes from turn to turn, so none of it can be part of a
    # cacheable prefix — which is exactly why it now follows the rules (#514).
    #
    # Named parts rather than one f-string: joined they are the same characters
    # as before, but they can also be measured, which is what #556 needs. Where
    # the prompt's bulk actually sits has been guessed at twice and the guess
    # went stale both times.
    athlete_sections: dict[str, str] = {
        "header": (
            f"\n\nAthlete data for this conversation\n"
            f"Today's date: {today}\n"
            f"{date_context}\n"
        ),
        "profile": f"Athlete profile: {json.dumps(profile)}\n",
        "race-profile": race_profile_section,
        "plan-past": (
            "Last 7 days of training (historical context, not upcoming): "
            f"{json.dumps(last_7_days)}\n"
        ),
        "plan-upcoming": (
            f"Upcoming plan (today and future only, next {len(next_n_days)} days): "
            f"{json.dumps(next_n_days)}"
        ),
        "assessment": assessment_section,
        "ride-metrics": metrics_section,
        "races": events_section,
        "weather": weather_section,
        "status-badge": status_badge_section,
        "training-load": training_load_section,
        "athlete-context": durable_context_section,
        "motivation-model": durable_motivation_section,
        # Next to the objective on purpose: the framing rule chains identity →
        # pattern → objective, and the two halves of that chain read together.
        "rider-identity": rider_identity_section(rider_identity),
        "athlete-model": durable_model_section,
        "performance-model": perf_model_section,
        "roi": roi_section,
        "hypotheses": hypotheses_section,
        "memory-facts": durable_memory_facts_section,
        "open-questions": durable_open_questions_section,
        "pinned-inquiries": pinned_inquiries_section,
        "coach-memory": memory_section,
        "workout": workout_section,
        "curiosity": workout_curiosity_section(workout_curiosity),
        "classification": classification_section,
        "science": science_section,
    }

    # The output contract stays last on purpose.  Response-format compliance is
    # the one thing that genuinely benefits from being the most recent
    # instruction, so it is not worth trading for the ~200 tokens it would add to
    # the cached prefix.  The weather rules join it here because they are
    # conditional, and the reasoning framework because its second step depends on
    # whether ride metrics exist.
    closing_instructions = (
        f"{weather_instructions}"
        "\n\nBefore writing your response, reason through: "
        "(1) what the athlete is really asking, "
        + (
            "(2) what their current CTL/ATL/TSB from actual rides suggests about their fatigue state, "
            if metrics_history_section
            else "(2) what their recent training history suggests about their fatigue state, "
        )
        + "(3) whether the request conflicts with training principles, "
        "(4) the most helpful coaching answer. "
        'Put this reasoning in a "thinking" field — it will not be shown to the athlete.\n\n'
        "ALWAYS respond with a valid JSON object containing exactly these fields:\n"
        '- "thinking": your internal reasoning (required, but never shown to the athlete)\n'
        '- "response": your natural language answer as a string (required)\n'
        '- "physiologyRationale": one short phrase capturing what the load/freshness/fatigue '
        "numbers alone suggest — your coach inference (use \"\" when not applicable)\n"
        '- "contextRationale": one short phrase capturing what this athlete\'s personal context '
        "suggests — a personal observation (use \"\" when not applicable)\n"
        '- "objectiveRationale": one short phrase naming what this buys the athlete in terms '
        "of the objective THEY train for — the payoff, not the mechanism "
        '(use "" when no objective is known)\n'
        '- "sources": an array of source titles you referenced from the science research section '
        "(omit or use [] if no research was cited)\n"
        '- "ride_note_update": optional object — only include when the athlete is describing a specific ride\n'
        f"{plan_updates_rule}"
    )

    return {
        "static": static_instructions,
        **athlete_sections,
        "closing": closing_instructions,
    }


def ask_trainer_system(*args, **kwargs) -> str:
    """The coach system prompt. Unchanged output; see the sections function."""
    return "".join(ask_trainer_system_sections(*args, **kwargs).values())


# ---------------------------------------------------------------------------
# update_coach_memory prompts
# ---------------------------------------------------------------------------


def update_memory_system() -> str:
    return (
        f"{COACH_PERSONA} Maintain concise, structured notes about an athlete.\n"
        "Extract any important, actionable information from this conversation exchange and update the notes.\n"
        "Keep notes under 400 words total. Organise notes under these categories (omit any category that has no relevant information):\n"
        "- Schedule constraints: preferred ride days, weekday time limits, work/life commitments affecting training availability.\n"
        "- Fatigue & intensity response: how the athlete subjectively responds to hard efforts, signs of over-reaching, recovery rate.\n"
        "- Hydration, fueling & heat response: actionable intake patterns and problems for long or hot sessions, such as drink volume, drink type, sodium/carbs, running out of fluids, bonking, cramping, GI issues, or poor heat tolerance.\n"
        "- Preferred workout types: favourite session formats, terrain preferences (e.g. loves hill climbing, prefers long endurance rides).\n"
        "- Recurring issues: repeated problems such as over-pacing endurance rides, skipping cooldowns, abandoning intervals early.\n"
        "- Psychological training tendencies: durable patterns in the athlete's relationship with training, rest, control, and motivation. "
        "Capture overtraining vs undertraining bias, nervousness or anxiety after multiple rest days, FOMO around missed rides or group sessions, "
        "overanalysis, reassurance seeking, needing permission to rest, tendency to do too much when feeling fresh, or psychological benefit from "
        "specific activities such as MTB or easy/social rides.\n"
        "- FTP & target context: record up to the 5 most recent FTP estimates with approximate dates; drop the oldest when adding a new one. Note current power/HR targets.\n"
        "- Race & event priorities: upcoming events, goal races, priority A/B/C designations, target dates.\n"
        "- Goals & motivations: overall training goals, personal motivations, rider strengths and weaknesses.\n"
        "Only update a category when new, durable information is present. "
        "A single event may be stored when it is materially actionable for future coaching, for example "
        "the athlete ran out of drink on a 5-hour ride in >30 C heat or reported a specific hydration/fueling "
        "amount that should shape future long-ride advice. "
        "Do not store one-off transient moods, nerves, worries, low motivation, or excitement as permanent psychological traits unless "
        "the athlete confirms the pattern, it repeats across exchanges, or it creates an actionable risk for recovery, intensity, or rest decisions. "
        "When storing psychological tendencies, phrase them cautiously with evidence strength, e.g. 'may need reassurance after rest days' rather than overconfident diagnoses. "
        "Return ONLY the updated notes as plain text. If nothing new and important was mentioned, return the existing notes unchanged."
    )


def update_memory_user(
    current_memory: str, user_message: str, coach_response: str
) -> str:
    return (
        f"Existing notes:\n{current_memory or '(none)'}\n\n"
        f"Latest exchange:\nAthlete: {user_message}\nCoach: {coach_response}\n\n"
        "Update the notes with any new important information. If the coach asked a targeted follow-up or recommendation clarification question and the athlete answered it, treat the answer as potentially important coaching context."
    )


# ---------------------------------------------------------------------------
# extract_athlete_facts prompts (import historical coach conversations)
# ---------------------------------------------------------------------------


def extract_athlete_facts_system() -> str:
    return (
        f"{COACH_PERSONA} You are mining a historical coaching conversation for "
        "DURABLE facts about the athlete that should inform future coaching.\n"
        "Extract only stable, actionable traits and preferences — not one-off events, "
        "transient moods, or workout-specific details.\n"
        "Focus on patterns such as: how the athlete responds to rest and hard efforts, "
        "what motivates them, what they worry about, when they seek reassurance, "
        "schedule and availability constraints, fueling/hydration/heat patterns, "
        "preferred and disliked workout types or terrain, recurring training mistakes, "
        "and psychological tendencies (overtraining bias, FOMO, rest anxiety, "
        "reassurance seeking, doing too much when fresh).\n"
        "Use one of these category slugs for each fact: schedule_constraints, "
        "fatigue_response, fueling_hydration, preferred_workouts, recurring_issues, "
        "psychological_tendencies, goals_motivation, coaching_risk, general.\n"
        "Assign a confidence between 0.3 and 0.9 reflecting how strongly the transcript "
        "supports a DURABLE pattern: repeated or explicitly confirmed patterns score "
        "higher; single mentions score lower. Never exceed 0.9.\n"
        "Phrase psychological tendencies cautiously with evidence strength, e.g. "
        "'may get anxious after two rest days' rather than an overconfident diagnosis.\n"
        "For each fact include a short verbatim-ish 'sourceSnippet' (<=200 chars) quoting "
        "or closely paraphrasing the transcript evidence.\n"
        "Deduplicate: merge near-identical observations into a single fact.\n"
        "Return up to 20 of the most coaching-relevant facts.\n"
        "ALWAYS respond with a valid JSON object of the form: "
        '{"candidates": [{"fact": str, "category": str, "confidence": number, '
        '"sourceSnippet": str}]}. '
        "Return an empty candidates array when the transcript contains no durable, "
        "actionable athlete information."
    )


def extract_athlete_facts_user(transcript: str) -> str:
    return (
        "Historical coaching conversation transcript:\n"
        "-----\n"
        f"{transcript}\n"
        "-----\n"
        "Extract the durable athlete facts as specified."
    )


# ---------------------------------------------------------------------------
# generate_athlete_insights prompts
# ---------------------------------------------------------------------------


def generate_athlete_insights_system() -> str:
    return (
        f"{COACH_PERSONA} You are periodically reviewing an athlete's accumulated "
        "training history to INFER durable insights that should inform future "
        "coaching. Unlike facts the athlete states directly, these are patterns you "
        "deduce from the objective record — so phrase them as data-supported "
        "observations, not certainties.\n"
        "Look for repeatable patterns across activities, for example:\n"
        "- how the athlete responds to rest (e.g. performs best after one recovery day)\n"
        "- environmental effects (e.g. performs better outdoors, tolerates heat well)\n"
        "- pacing habits within a session (e.g. consistently negative-splits VO2 intervals)\n"
        "- how load, sleep, or feedback correlates with performance or how a ride felt\n"
        "- workout types or terrain where the athlete over- or under-performs\n"
        "- recurring execution issues (e.g. drifts above target power on long endurance rides).\n"
        "Only report a pattern when at least two activities support it; a single ride is "
        "an event, not an insight. Ignore one-off results and normal day-to-day variation.\n"
        "Do NOT restate insights already present in the provided existing observations, "
        "and do not simply echo a single ride's coach/athlete note — synthesise across rides.\n"
        "Classify each insight with a 'kind':\n"
        "- 'fact' for a stable, quantifiable value the history establishes about the "
        "athlete (e.g. an estimated FTP, a max heart rate, a typical resting HR).\n"
        "- 'observation' for a pattern of repeated behaviour or response (e.g. fades late "
        "in intervals, prefers MTB, recovers fast after hard days).\n"
        "When unsure use 'observation' — most inferred patterns are observations, not facts.\n"
        "Use one of these category slugs for each insight: fatigue_response, "
        "fueling_hydration, preferred_workouts, recurring_issues, "
        "psychological_tendencies, goals_motivation, coaching_risk, general.\n"
        "Assign a confidence between 0.3 and 0.9 reflecting how strongly the history "
        "supports a DURABLE pattern: many consistent activities score higher, a pattern "
        "seen only twice scores low. Never exceed 0.9 — these are inferred, not confirmed.\n"
        "For each insight include a 'sourceSnippet' (<=200 chars) citing the concrete "
        "evidence from the history (e.g. dates, metrics, or the number of rides).\n"
        "Return up to 8 of the most coaching-relevant insights.\n"
        "ALWAYS respond with a valid JSON object of the form: "
        '{"candidates": [{"fact": str, "kind": "fact"|"observation", "category": str, '
        '"confidence": number, "sourceSnippet": str}]}. '
        "Return an empty candidates array when the history is too thin or shows no "
        "durable pattern."
    )


def generate_athlete_insights_user(
    metrics_section: str, existing_facts: list[str] | None = None
) -> str:
    existing = existing_facts or []
    existing_section = (
        "Existing observations already on file (do not repeat these):\n"
        + "\n".join(f"- {fact}" for fact in existing)
        if existing
        else "No observations are on file yet."
    )
    return (
        f"{metrics_section}\n\n"
        f"{existing_section}\n\n"
        "Infer new durable athlete insights from this training history as specified."
    )


# ---------------------------------------------------------------------------
# derive_athlete_model prompts (#384: long-term structured athlete model)
# ---------------------------------------------------------------------------


def derive_athlete_model_system() -> str:
    return (
        f"{COACH_PERSONA} You maintain a structured LONG-TERM MODEL of the athlete: "
        "the slow-changing physiological and performance characteristics that define "
        "who they are as a rider, distinct from any single session.\n"
        "You are given the athlete's recent training history and their current model "
        "(which may be blank). Produce an UPDATED model that reflects the evidence.\n"
        "Fields:\n"
        "- ftpWatts: best estimate of functional threshold power in watts (integer), "
        "or null if the history gives no basis for it. Do not invent precision.\n"
        "- vo2max: estimate of VO2 max in ml/kg/min (number) only if strongly "
        "supported, else null.\n"
        "- pacingQuality, recoveryAbility, thresholdDurability, heatTolerance, "
        "preferredTrainingStyle: short qualitative descriptors (<=120 chars each), "
        "e.g. 'holds threshold well up to ~30 min', 'recovers fast after hard days', "
        "'fades in heat above 28C'. Use '' when there is no evidence.\n"
        "- strengths, weaknesses, riskFactors: arrays of short phrases (each <=80 "
        "chars); [] when unknown.\n"
        "- summary: one to three sentences capturing the athlete's durable profile; "
        "'' when there is nothing durable to say.\n"
        "- confidence: number 0.0-0.9 for how well the history supports the model as a "
        "whole. Never exceed 0.9 — this is inferred, not measured.\n"
        "Carry forward well-supported values already in the current model when new "
        "history does not contradict them; only change a field when the evidence "
        "warrants it. Prefer keeping a field empty over guessing.\n"
        "ALWAYS respond with a single valid JSON object with exactly these keys: "
        '{"ftpWatts": int|null, "vo2max": number|null, "pacingQuality": str, '
        '"recoveryAbility": str, "thresholdDurability": str, "heatTolerance": str, '
        '"preferredTrainingStyle": str, "strengths": [str], "weaknesses": [str], '
        '"riskFactors": [str], "summary": str, "confidence": number}.'
    )


def derive_athlete_model_user(
    metrics_section: str, current_model: dict | None = None
) -> str:
    current = current_model or {}
    if current:
        current_section = (
            "Current athlete model on file (refine, do not blindly discard):\n"
            f"{json.dumps(current, ensure_ascii=False)}"
        )
    else:
        current_section = "No athlete model exists yet; build one from the history."
    return (
        f"{metrics_section}\n\n"
        f"{current_section}\n\n"
        "Return the updated long-term athlete model as specified."
    )


# ---------------------------------------------------------------------------
# generate_athlete_hypotheses prompts
# ---------------------------------------------------------------------------


def generate_athlete_hypotheses_system() -> str:
    return (
        f"{COACH_PERSONA} You are reviewing an athlete's accumulated training "
        "history to form explicit HYPOTHESES — tentative, testable ideas about "
        "cause and effect that are worth tracking but NOT yet trusted enough to "
        "act on.\n"
        "A hypothesis is different from a durable insight: an insight is a pattern "
        "the record already supports well, whereas a hypothesis is a plausible "
        "explanation or prediction that still NEEDS VALIDATION — for example "
        "'upper-body strength training suppresses heart-rate response the "
        "following day' or 'the athlete rides stronger in the second half of a "
        "training block'.\n"
        "Propose a hypothesis only when the history gives at least a hint worth "
        "testing (roughly two supporting activities); a single ride is not enough. "
        "Prefer causal or predictive claims the athlete could confirm or refute "
        "over time. Do NOT restate ideas already present in the provided existing "
        "hypotheses or observations.\n"
        "A hypothesis MUST be able to recur, or it can never be confirmed or "
        "refuted and is worthless. Concretely:\n"
        "- ONE condition and ONE outcome. Never chain conditions: 'after a "
        "consecutive strength and high-intensity cycling day, a yoga session "
        "followed by…' describes a situation that will not happen twice.\n"
        "- The condition must be something this athlete meets at least monthly. If "
        "you cannot point to it happening twice in the history, do not propose it.\n"
        "- Keep the statement under 140 characters and free of dates, one-off "
        "circumstances and hedging clauses. Statements are matched by their exact "
        "wording, so write it the way you would write it again next month for the "
        "same pattern — otherwise it will be stored as a brand-new idea instead of "
        "gaining support.\n"
        "- Prefer 'strength training the day before suppresses heart-rate response' "
        "over a sentence describing one particular week.\n"
        "Propose FEWER, more repeatable hypotheses rather than many specific ones. "
        "Two that can accumulate evidence are worth more than five that cannot.\n"
        "Use one of these category slugs for each hypothesis: fatigue_response, "
        "fueling_hydration, preferred_workouts, recurring_issues, "
        "psychological_tendencies, goals_motivation, coaching_risk, general.\n"
        "Assign a confidence between 0.2 and 0.6 — these are unproven ideas, so "
        "keep it low; never exceed 0.6. For each hypothesis include a 'rationale' "
        "(<=200 chars) citing the concrete evidence (dates, metrics, or number of "
        "rides) that motivates testing it.\n"
        "Return up to 5 of the most coaching-relevant hypotheses.\n"
        "ALWAYS respond with a valid JSON object of the form: "
        '{"candidates": [{"statement": str, "category": str, "confidence": number, '
        '"rationale": str}]}. '
        "Return an empty candidates array when the history is too thin or suggests "
        "no idea worth testing."
    )


def generate_athlete_hypotheses_user(
    metrics_section: str,
    existing_facts: list[str] | None = None,
    existing_hypotheses: list[str] | None = None,
) -> str:
    facts = existing_facts or []
    hypotheses = existing_hypotheses or []
    facts_section = (
        "Observations already on file:\n"
        + "\n".join(f"- {fact}" for fact in facts)
        if facts
        else "No observations are on file yet."
    )
    hypotheses_section = (
        "Hypotheses already being tracked (do not repeat these):\n"
        + "\n".join(f"- {item}" for item in hypotheses)
        if hypotheses
        else "No hypotheses are on file yet."
    )
    return (
        f"{metrics_section}\n\n"
        f"{facts_section}\n\n"
        f"{hypotheses_section}\n\n"
        "Form new testable hypotheses about this athlete from the training history "
        "as specified."
    )


# ---------------------------------------------------------------------------
# generate_open_questions prompts (#385)
# ---------------------------------------------------------------------------


def generate_open_questions_system() -> str:
    return (
        f"{COACH_PERSONA} You are maintaining an athlete's OPEN QUESTIONS list — the "
        "specific things about this athlete you cannot yet answer from the record "
        "and want to resolve. An open question is NOT a hypothesis (a tentative "
        "answer) and NOT an insight (a supported pattern); it is an honest, "
        "coaching-relevant uncertainty, for example 'Is FTP underestimated?', "
        "'Does the MTB position improve sustainable power?', or 'Does strength "
        "training suppress the heart-rate response?'.\n"
        "For each question provide: a 'question' (<=160 chars, phrased as a real "
        "question), 'evidence' (<=200 chars, the concrete signal in the history "
        "that raises it — dates, metrics, or number of rides; empty string if "
        "none yet), 'needs' (<=200 chars, what would answer it: a specific test, "
        "a controlled comparison, or a number of further observations), and a "
        "category slug from: fatigue_response, fueling_hydration, "
        "preferred_workouts, recurring_issues, psychological_tendencies, "
        "goals_motivation, coaching_risk, general.\n"
        "Only raise a question the training data genuinely leaves open and that "
        "would change your coaching once answered. Do NOT repeat questions already "
        "on file, and do NOT invent trivia. If the history now clearly answers a "
        "question already on file, return it with 'resolved': true and a short "
        "'resolution' (<=200 chars) stating the answer.\n"
        "Return up to 5 of the most coaching-relevant questions, most important "
        "first.\n"
        "ALWAYS respond with a valid JSON object of the form: "
        '{"candidates": [{"question": str, "evidence": str, "needs": str, '
        '"category": str, "resolved": bool, "resolution": str}]}. '
        "Return an empty candidates array when the history leaves no useful "
        "question open."
    )


def generate_open_questions_user(
    metrics_section: str,
    existing_facts: list[str] | None = None,
    existing_questions: list[str] | None = None,
) -> str:
    facts = existing_facts or []
    questions = existing_questions or []
    facts_section = (
        "Observations already on file:\n"
        + "\n".join(f"- {fact}" for fact in facts)
        if facts
        else "No observations are on file yet."
    )
    questions_section = (
        "Open questions already tracked (do not repeat; resolve one only if the "
        "history now answers it):\n"
        + "\n".join(f"- {item}" for item in questions)
        if questions
        else "No open questions are on file yet."
    )
    return (
        f"{metrics_section}\n\n"
        f"{facts_section}\n\n"
        f"{questions_section}\n\n"
        "Maintain this athlete's open questions list from the training history as "
        "specified."
    )


# ---------------------------------------------------------------------------
# athlete inquiry prompts (#506)
# ---------------------------------------------------------------------------

# The category slugs shared by every athlete-knowledge generator.
_ATHLETE_KNOWLEDGE_CATEGORIES = (
    "fatigue_response, fueling_hydration, preferred_workouts, recurring_issues, "
    "psychological_tendencies, goals_motivation, coaching_risk, general"
)


def generate_inquiries_system(max_candidates: int) -> str:
    """System prompt for raising questions only the athlete can answer (#506).

    The whole value of this pass is the gate it puts in front of itself: the model
    must first work out what the incoming training data *will* tell it, and may
    only ask about the residual. Without that step a coach model happily asks
    about FTP, fatigue and pacing — all of which the next few rides answer for
    free — and burns the athlete's patience on questions it should infer.
    """
    return (
        f"{COACH_PERSONA} You are deciding what to ASK THIS ATHLETE DIRECTLY.\n"
        "You already receive every ride they do: duration, power, heart rate, "
        "cadence, speed, elevation, weather and how the session compared with the "
        "plan. Weekly passes turn that stream into observations, hypotheses and "
        "open questions on their own.\n"
        "Work in two steps.\n"
        "STEP 1 — Ask yourself what the NEXT FEW WEEKS OF DATA WILL TELL YOU "
        "without anyone saying a word: fitness trend, threshold drift, how they "
        "respond to load, which sessions they complete, pacing, durability, "
        "weather tolerance. You must NOT ask about any of it. If more riding "
        "would settle it, it is an open question or an experiment, not a question "
        "for the athlete.\n"
        "STEP 2 — Ask only about what the data can NEVER reveal, because it lives "
        "in the athlete's head, body or calendar rather than in their power file. "
        "For example: WHY a session was skipped or cut short, whether a pain or "
        "niggle is still there, what they actually want from this season, "
        "constraints on their week you cannot see, equipment or measurement "
        "changes (a swapped power meter, a new bike), illness, sleep, stress or "
        "life events, and what they enjoy or dread doing.\n"
        "Each question must be one plain, specific, conversational question a "
        "coach would actually ask — not a form field, not two questions bolted "
        "together, and never a request for a number the athlete has no way to "
        "know.\n"
        "For each provide: 'question' (<=200 chars, addressed to the athlete as "
        "'you'), 'whyAsking' (<=200 chars, why the training data cannot tell you "
        "this and what you would do differently once you knew — the athlete sees "
        "this), 'settingsHint' (<=120 chars, where in the app's settings they "
        "could state this themselves, e.g. 'Settings > Athlete > Goals'; empty "
        f"string if nowhere fits), and a category slug from: "
        f"{_ATHLETE_KNOWLEDGE_CATEGORIES}.\n"
        "Do NOT repeat anything already known, already asked, or already answered. "
        "Silence is a valid answer: return an empty array unless a question would "
        "genuinely change how you coach them.\n"
        f"Return at most {max_candidates}, the most consequential first.\n"
        "ALWAYS respond with a valid JSON object of the form: "
        '{"candidates": [{"question": str, "whyAsking": str, '
        '"settingsHint": str, "category": str}]}.'
    )


def generate_inquiries_user(
    metrics_section: str,
    existing_facts: list[str] | None = None,
    asked_questions: list[str] | None = None,
) -> str:
    facts = existing_facts or []
    asked = asked_questions or []
    facts_section = (
        "What you already know about this athlete:\n"
        + "\n".join(f"- {fact}" for fact in facts)
        if facts
        else "You know nothing durable about this athlete yet."
    )
    asked_section = (
        "Questions you have ALREADY put to this athlete — never ask these again, "
        "in any wording:\n" + "\n".join(f"- {item}" for item in asked)
        if asked
        else "You have not asked this athlete anything yet."
    )
    return (
        f"{metrics_section}\n\n"
        f"{facts_section}\n\n"
        f"{asked_section}\n\n"
        "Decide what — if anything — you need to ask this athlete directly, as "
        "specified."
    )


def evaluate_inquiry_answer_system(is_final_attempt: bool) -> str:
    """System prompt judging whether a reply actually answered the question (#506).

    ``is_final_attempt`` switches the fallback from "rephrase and ask again" to
    "stop asking and hand off to Settings", so the coach never puts the same
    question a third time.
    """
    fallback = (
        (
            "This was your LAST attempt. Do NOT ask again. In 'reply', thank them, "
            "say plainly that you will work without it for now, and point them at "
            "the settings location given below so they can state it themselves "
            "whenever they want. Leave 'question' empty."
        )
        if is_final_attempt
        else (
            "Ask ONCE more. In 'question' put the rephrased question (<=200 chars) "
            "— make it easier to answer than before: narrow it, give an example "
            "answer, or offer the likely options. In 'reply' write the friendly "
            "one-or-two-sentence message that carries it, acknowledging what they "
            "did say. Never imply they answered badly."
        )
    )
    return (
        f"{COACH_PERSONA} You asked your athlete a question and they replied. "
        "Judge ONLY whether the reply actually answers what you asked.\n"
        "Be generous: a short, vague or partial answer that still tells you the "
        "thing you needed ('knee's fine now', 'work trip') COUNTS as answered. An "
        "answer counts as NOT answered only when it is off-topic, a refusal, a "
        "question back at you, or so unclear it tells you nothing.\n"
        "If it IS answered: set 'answered' true and put in 'fact' a single "
        "third-person sentence (<=200 chars) recording what you now know about the "
        "athlete, phrased so it still reads correctly in six weeks (resolve 'last "
        "Tuesday' and similar to what actually happened, and keep it free of "
        "wording that would age badly). In 'reply' write a short, warm "
        "acknowledgement — one or two sentences, saying what you will do with it. "
        "Leave 'question' empty.\n"
        f"If it is NOT answered: set 'answered' false. {fallback} Leave 'fact' "
        "empty.\n"
        "ALWAYS respond with a valid JSON object of the form: "
        '{"answered": bool, "fact": str, "reply": str, "question": str}.'
    )


def evaluate_inquiry_answer_user(
    question: str,
    answer: str,
    why_asking: str = "",
    settings_hint: str = "",
) -> str:
    why_line = (
        f"Why you asked: {why_asking}\n" if why_asking.strip() else ""
    )
    hint_line = (
        f"Where they could state this themselves: {settings_hint}\n"
        if settings_hint.strip()
        else "There is no settings page for this; suggest they simply tell you in "
        "the chat when they know.\n"
    )
    return (
        f"You asked: {question}\n"
        f"{why_line}"
        f"They replied: {answer}\n"
        f"{hint_line}\n"
        "Judge the reply as specified."
    )


# ---------------------------------------------------------------------------
# generate_validation_experiments prompts
# ---------------------------------------------------------------------------


def generate_validation_experiments_system() -> str:
    return (
        f"{COACH_PERSONA} An athlete has open questions the record cannot yet "
        "answer — unproven HYPOTHESES that still need validation. Rather than "
        "guess, your job is to propose concrete VALIDATION EXPERIMENTS: small, "
        "repeatable sessions or comparisons the athlete can actually run to settle "
        "each question with data.\n"
        "Good experiments are specific and controlled — they change one variable "
        "and produce a clear signal. For example 'compare both bikes over the same "
        "climb using identical power pedals', 'repeat the VO2 session with shorter "
        "recoveries', 'perform a 30-minute threshold test to validate FTP', or "
        "'repeat the session under cooler conditions'.\n"
        "For each experiment provide a 'question' (the uncertainty it resolves, "
        "restating the hypothesis in plain terms), a 'protocol' (<=200 chars, the "
        "exact session/comparison to run and the one variable it isolates), a "
        "'rationale' (<=200 chars, what a result would tell you and how to read "
        "it), and a category slug from: fatigue_response, fueling_hydration, "
        "preferred_workouts, recurring_issues, psychological_tendencies, "
        "goals_motivation, coaching_risk, general.\n"
        "Only propose an experiment that is realistic for a normal training week "
        "and that would meaningfully reduce the uncertainty. Do NOT repeat "
        "experiments already suggested. Return up to 5, the most decisive first.\n"
        "ALWAYS respond with a valid JSON object of the form: "
        '{"candidates": [{"question": str, "protocol": str, "rationale": str, '
        '"category": str}]}. '
        "Return an empty candidates array when no useful experiment can be designed."
    )


def generate_validation_experiments_user(
    uncertainties_section: str,
    existing_experiments: list[str] | None = None,
) -> str:
    experiments = existing_experiments or []
    experiments_section = (
        "Experiments already suggested (do not repeat these):\n"
        + "\n".join(f"- {item}" for item in experiments)
        if experiments
        else "No experiments have been suggested yet."
    )
    return (
        f"{uncertainties_section}\n\n"
        f"{experiments_section}\n\n"
        "Design validation experiments that would let the athlete confirm or "
        "refute the open questions above, as specified."
    )


# ---------------------------------------------------------------------------
# generate_athlete_predictions prompts
# ---------------------------------------------------------------------------


def generate_athlete_predictions_system() -> str:
    return (
        f"{COACH_PERSONA} To keep yourself honest and measure your coaching "
        "quality, you make explicit, forward-looking PREDICTIONS about the "
        "athlete that can later be checked against what actually happens.\n"
        "A good prediction is specific, time-bound, and falsifiable — for example "
        "'the athlete will be fully recovered and ready for a hard session "
        "tomorrow', 'the athlete will hit the interval targets in the next VO2 "
        "workout', or 'fatigue will force an easier week within the next 10 "
        "days'. Avoid vague statements you could never score as right or wrong.\n"
        "For each prediction provide a 'prediction' (the claim, <=200 chars), an "
        "'expectedOutcome' (<=200 chars, the concrete, observable result that "
        "would confirm it and how you would check it), a 'horizon' (<=60 chars, "
        "when it can be checked, e.g. 'tomorrow' or 'within 2 weeks'), a "
        "'confidence' between 0.3 and 0.9 (how sure you are), and a category slug "
        "from: fatigue_response, fueling_hydration, preferred_workouts, "
        "recurring_issues, psychological_tendencies, goals_motivation, "
        "coaching_risk, general.\n"
        "Only make a prediction the training history genuinely supports and that "
        "could realistically be checked from future activity data. Do NOT repeat "
        "predictions already on file. Return up to 5, the most testable first.\n"
        "ALWAYS respond with a valid JSON object of the form: "
        '{"candidates": [{"prediction": str, "expectedOutcome": str, '
        '"horizon": str, "confidence": number, "category": str}]}. '
        "Return an empty candidates array when the history supports no confident, "
        "checkable prediction."
    )


def generate_athlete_predictions_user(
    metrics_section: str,
    existing_predictions: list[str] | None = None,
) -> str:
    predictions = existing_predictions or []
    predictions_section = (
        "Predictions already on file (do not repeat these):\n"
        + "\n".join(f"- {item}" for item in predictions)
        if predictions
        else "No predictions are on file yet."
    )
    return (
        f"{metrics_section}\n\n"
        f"{predictions_section}\n\n"
        "Make new, checkable predictions about this athlete from the training "
        "history as specified."
    )


# ---------------------------------------------------------------------------
# evaluate_athlete_predictions prompts
# ---------------------------------------------------------------------------


def evaluate_athlete_predictions_system() -> str:
    return (
        f"{COACH_PERSONA} You are scoring PREDICTIONS you made earlier against the "
        "athlete's actual recent training data to measure how good your coaching "
        "calls have been.\n"
        "You are given numbered predictions, each with the outcome that would "
        "confirm it, and a summary of recent activities. For each prediction "
        "decide whether the data now shows it came TRUE or FALSE.\n"
        "Only score a prediction you can actually judge from the data. If the "
        "outcome cannot yet be observed — not enough time has passed, or the "
        "relevant activity has not happened — mark its verdict 'unknown' and it "
        "will be left pending. Hold a fair bar: do not call a prediction correct "
        "on weak or absent evidence, and do not call it wrong merely because the "
        "data is silent.\n"
        "For each prediction you can score, give its zero-based 'index', a "
        "'verdict' of 'correct' or 'incorrect', and an 'actualOutcome' (<=200 "
        "chars) stating plainly what actually happened, citing the concrete "
        "evidence (metrics, dates, or number of rides). Omit predictions you "
        "cannot yet judge, or give them the 'unknown' verdict.\n"
        "ALWAYS respond with a valid JSON object of the form: "
        '{"evaluations": [{"index": int, "verdict": str, "actualOutcome": str}]}. '
        "Return an empty evaluations array when none of the predictions can be "
        "scored yet."
    )


def evaluate_athlete_predictions_user(
    metrics_section: str,
    predictions: list[dict[str, str]],
) -> str:
    lines = []
    for index, item in enumerate(predictions):
        line = f"{index}. {item['prediction']}"
        expected = item.get("expected_outcome")
        if expected:
            line += f" | confirmed if: {expected}"
        horizon = item.get("horizon")
        if horizon:
            line += f" | check by: {horizon}"
        lines.append(line)
    predictions_block = "\n".join(lines)
    return (
        f"{metrics_section}\n\n"
        "Predictions you made earlier (index. prediction | confirmed if | check by):\n"
        "-----\n"
        f"{predictions_block}\n"
        "-----\n"
        "Score each prediction you can now judge from the data, as specified."
    )


# ---------------------------------------------------------------------------
# detect_athlete_fact_contradictions prompts
# ---------------------------------------------------------------------------


def detect_contradictions_system() -> str:
    return (
        f"{COACH_PERSONA} You are checking an athlete's STORED knowledge against "
        "their recent training data to catch facts the new evidence contradicts.\n"
        "You are given numbered stored facts and a summary of recent activities. "
        "Report ONLY a stored fact when the training data clearly and materially "
        "disagrees with it — for example a stored FTP of 320 W while the athlete "
        "repeatedly holds 400 W for 4-minute intervals, a 'dislikes long rides' "
        "fact contradicted by several 4-hour endurance rides, or a 'struggles in "
        "heat' fact contradicted by strong warm-weather performances.\n"
        "Hold a high bar. Do NOT flag a fact merely because the data does not "
        "mention it, because of a single off day, or because of normal day-to-day "
        "variation — absence of evidence is not contradiction. When in doubt, do "
        "not flag it.\n"
        "For each contradicted fact, give its zero-based 'factIndex' and a concise "
        "'reason' (<=280 chars), phrased as the coach would explain it to the "
        "athlete, citing the concrete evidence (metrics, dates, or number of "
        "rides) and noting that the fact should be re-validated.\n"
        "ALWAYS respond with a valid JSON object of the form: "
        '{"contradictions": [{"factIndex": int, "reason": str}]}. '
        "Return an empty contradictions array when the evidence is consistent with "
        "the stored facts."
    )


def detect_contradictions_user(metrics_section: str, facts: list[str]) -> str:
    fact_lines = "\n".join(f"{index}. {fact}" for index, fact in enumerate(facts))
    return (
        f"{metrics_section}\n\n"
        "Stored facts about the athlete (index. fact):\n"
        "-----\n"
        f"{fact_lines}\n"
        "-----\n"
        "Identify which stored facts the recent training data contradicts, as "
        "specified."
    )


# ---------------------------------------------------------------------------
# match_observations_to_recommendations prompts
# ---------------------------------------------------------------------------


def match_observations_system() -> str:
    return (
        f"{COACH_PERSONA} You are deciding how the coach's durable observations "
        "about an athlete (habits, tendencies, flaws) should support today's "
        "training recommendations.\n"
        "For EACH observation, pick the single recommendation it most directly "
        "reinforces or qualifies — the one where knowing this about the athlete "
        "most changes how the advice should be received.\n"
        "Use the zero-based index of the recommendation. If an observation is not "
        "relevant to any recommendation, use null.\n"
        "ALWAYS respond with a valid JSON object of the form: "
        '{"assignments": [{"observationIndex": int, "recommendationIndex": int|null}]}. '
        "Include exactly one entry per observation."
    )


def match_observations_user(
    recommendations: list[dict], observations: list[str]
) -> str:
    rec_lines = "\n".join(
        f"{index}. {rec.get('recommendation', '')}"
        for index, rec in enumerate(recommendations)
    )
    obs_lines = "\n".join(
        f"{index}. {observation}" for index, observation in enumerate(observations)
    )
    return (
        "Today's recommendations (index. text):\n"
        "-----\n"
        f"{rec_lines}\n"
        "-----\n"
        "Observations about the athlete (index. text):\n"
        "-----\n"
        f"{obs_lines}\n"
        "-----\n"
        "Match each observation to the recommendation it best supports."
    )


# ---------------------------------------------------------------------------
# rate_completed_workout prompts
# ---------------------------------------------------------------------------


def rate_workout_system() -> str:
    return (
        f"{COACH_PERSONA} Review a completed training session. "
        "Compare what was PLANNED against what the athlete ACTUALLY DID. "
        "First check whether the actual activity type/character matches the planned type — for example, "
        "if an endurance ride was planned but intervals were performed, or vice versa, call that out "
        "explicitly and explain the training impact. Then comment on the numbers: "
        "when objective stream data is available (power, HR, time-in-zone), use it to give "
        "precise, actionable insights — e.g. 'You went 15 % over Z2 intensity in the first 30 min, "
        "which erodes your aerobic base and costs recovery'. Otherwise use the hand-entered metrics. "
        "Give the response in 2-4 sentences, warm and personal.\n\n"
        "IMPORTANT — follow-up dialogue rules:\n"
        "When the activity data is SHORT (actual duration < 30 min or < 40 % of planned duration), "
        "LOW-CONFIDENCE (no power/HR data and no athlete notes), or AMBIGUOUS (activity character "
        "does not match the plan and the reason is unclear), do NOT confidently prescribe the "
        "next hard workout. Instead, set needs_athlete_feedback=true and populate follow_up_question "
        "with ONE concise, open-ended question that will help you understand the context — e.g. "
        "'This looks like a short easy spin rather than a full endurance session. Was it intentional "
        "recovery, a commute, or did you cut it short?' "
        "For normal, high-confidence sessions, set needs_athlete_feedback=false and "
        "follow_up_question=null.\n\n"
        "Response quality rules (apply to the 'feedback' field):\n"
        "- Acknowledge uncertainty explicitly when data is weak or ambiguous — never fabricate confidence.\n"
        "- Reference at least one concrete detail from this specific activity (duration, power number, "
        "perceived effort, or the athlete's own note) to show the feedback is tailored, not generic.\n"
        "- Give one clear, actionable next step (e.g. what to focus on next session, or what to watch).\n"
        "- Ask at most one follow-up question when clarification is needed — never stack multiple questions.\n"
        "- Never claim that an activity under 20 minutes produced meaningful endurance adaptation; "
        "a short spin is recovery or a warm-up, nothing more.\n\n"
        "Examples of well-formed feedback:\n\n"
        "SHORT RECOVERY SPIN (planned: 90 min endurance, actual: 18 min easy):\n"
        '  feedback: "That 18-minute spin is more of a leg-loosener than a training stimulus — not a '
        "problem if it was intentional recovery, but it won't count as your endurance work for the week. "
        'Was this a deliberate easy day, or did something cut the ride short?"\n'
        "  needs_athlete_feedback: true\n\n"
        "OVER-PACED ENDURANCE RIDE (planned: 90 min Z2, actual: 85 min with 25 % in Z3/Z4):\n"
        '  feedback: "Good endurance volume — 85 minutes is close to the full session. The issue is '
        "roughly 20 minutes crept into Z3/Z4, which turns base-building into a moderate-effort grind "
        "and slows recovery. For next time, keep a lid on effort in the first half and let HR guide you "
        'back into Z2."\n'
        "  needs_athlete_feedback: false\n\n"
        "MISSED/ABORTED WORKOUT (planned: 60 min intervals, actual: none or marked aborted):\n"
        "  feedback: \"Looks like the interval session didn't happen today — that's okay, life gets in "
        "the way. Do you want to shift it to tomorrow, or would you prefer I swap it for something "
        'shorter given your schedule?"\n'
        "  needs_athlete_feedback: true\n\n"
        "SUCCESSFUL INTERVAL DAY (planned: 4×8 min threshold, actual: 4×8 min on target):\n"
        '  feedback: "Really solid threshold session — you hit all four 8-minute blocks within target '
        "power and HR stayed controlled throughout. That kind of consistency is exactly what builds "
        'sustainable top-end fitness. Keep the next ride easy so this work can land properly."\n'
        "  needs_athlete_feedback: false\n\n"
        "Return ONLY a valid JSON object with these fields:\n"
        '- "feedback": your 2-4 sentence coaching response as a string\n'
        '- "flag_for_adaptation": true when the athlete should adapt their upcoming plan '
        "(perceived effort ≫ planned intensity, actual duration significantly shorter than "
        "planned, or athlete notes indicate fatigue/illness/pain); otherwise false\n"
        '- "needs_athlete_feedback": true when the ride is short, low-confidence, or ambiguous '
        "and you need the athlete to clarify before giving a confident verdict; otherwise false\n"
        '- "follow_up_question": a single concise follow-up question string when needs_athlete_feedback '
        "is true, or null when not needed\n"
        '- "suggested_feedback_tags": a JSON array of short tag strings (e.g. ["recovery", "commute", '
        '"cut_short", "illness"]) that represent plausible explanations the athlete can confirm; '
        "use an empty array when not applicable"
    )


def rate_workout_user(
    day: dict,
    feedback: dict | None,
    profile: dict | None = None,
    stream_delta: dict | None = None,
    actual_ride_analysis: dict | None = None,
) -> str:
    feedback = feedback or {}
    planned_power = ""
    if day.get("targetPower"):
        planned_power = f"\n- Target power: {day['targetPower']['low']}–{day['targetPower']['high']}W"
    planned_hr = ""
    if day.get("targetHeartRate"):
        planned_hr = f"\n- Target HR: {day['targetHeartRate']['low']}–{day['targetHeartRate']['high']} bpm"
    actual_power = (
        f"\n- Average power: {feedback['averagePower']}W"
        if feedback.get("averagePower")
        else ""
    )
    actual_peak = (
        f"\n- Peak power: {feedback['peakPower']}W" if feedback.get("peakPower") else ""
    )
    actual_hr = (
        f"\n- Average HR: {feedback['averageHeartRate']} bpm"
        if feedback.get("averageHeartRate")
        else ""
    )
    notes = f"\n- Notes: {feedback['notes']}" if feedback.get("notes") else ""

    effort_labels = {
        1: "Easy",
        2: "Moderate",
        3: "Hard",
        4: "Very Hard",
        5: "Max",
    }

    profile_section = f"\n\nAthlete profile: {json.dumps(profile)}" if profile else ""

    # --- Strava stream delta section ---
    delta_section = ""
    if stream_delta:
        lines: list[str] = ["\n\nObjective stream data (from Strava):"]

        # Power
        if stream_delta.get("avg_power_w") is not None:
            lines.append(f"- Actual avg power: {stream_delta['avg_power_w']}W")
        if stream_delta.get("normalized_power_w") is not None:
            lines.append(
                f"- Normalized power (NP): {stream_delta['normalized_power_w']}W"
            )
        if stream_delta.get("target_power_low") is not None:
            lines.append(
                f"- Target power: {stream_delta['target_power_low']}–{stream_delta['target_power_high']}W"
            )
        if stream_delta.get("avg_power_delta_pct") is not None:
            sign = "+" if stream_delta["avg_power_delta_pct"] >= 0 else ""
            lines.append(
                f"- Avg power vs target midpoint: {sign}{stream_delta['avg_power_delta_pct']} %"
            )
        if stream_delta.get("normalized_power_delta_pct") is not None:
            sign = "+" if stream_delta["normalized_power_delta_pct"] >= 0 else ""
            lines.append(
                f"- NP vs target midpoint: {sign}{stream_delta['normalized_power_delta_pct']} %"
            )

        # Time in zones
        tiz = stream_delta.get("time_in_zones")
        if tiz:
            zone_labels = {
                "z1_secs": "Z1 (<55 % FTP)",
                "z2_secs": "Z2 (55–75 % FTP)",
                "z3_secs": "Z3 (75–90 % FTP)",
                "z4_secs": "Z4 (90–105 % FTP)",
                "z5_secs": "Z5 (105–120 % FTP)",
                "z6_secs": "Z6 (120–150 % FTP)",
                "z7_secs": "Z7 (>150 % FTP)",
            }
            tiz_parts = [
                f"{label}: {round(tiz[key] / 60)} min"
                for key, label in zone_labels.items()
                if tiz.get(key, 0) > 0
            ]
            if tiz_parts:
                lines.append(f"- Time in zones: {', '.join(tiz_parts)}")

        # HR
        if stream_delta.get("avg_hr_bpm") is not None:
            lines.append(f"- Actual avg HR: {stream_delta['avg_hr_bpm']} bpm")
        if stream_delta.get("target_hr_low") is not None:
            lines.append(
                f"- Target HR: {stream_delta['target_hr_low']}–{stream_delta['target_hr_high']} bpm"
            )
        if stream_delta.get("avg_hr_delta_pct") is not None:
            sign = "+" if stream_delta["avg_hr_delta_pct"] >= 0 else ""
            lines.append(
                f"- Avg HR vs target midpoint: {sign}{stream_delta['avg_hr_delta_pct']} %"
            )
        if stream_delta.get("hr_drift_bpm") is not None:
            drift = stream_delta["hr_drift_bpm"]
            direction = "rising" if drift > 0 else "falling"
            lines.append(
                f"- HR drift across session: {abs(drift):.1f} bpm ({direction})"
            )

        # Intensity spikes
        spikes = stream_delta.get("intensity_spikes") or []
        for spike in spikes:
            lines.append(
                f"- Intensity spike {spike['start_min']}–{spike['end_min']} min: "
                f"{spike['avg_power_w']}W avg ({spike['pct_over_target']:+.1f} % over target)"
            )

        delta_section = "\n".join(lines)

    # --- Actual activity analysis section (from algorithmic stream analysis) ---
    actual_analysis_section = ""
    if actual_ride_analysis:
        category = actual_ride_analysis.get("ride_category", "unknown")
        avg_pwr = actual_ride_analysis.get("avg_power_w")
        intervals = actual_ride_analysis.get("intervals_detected") or []
        confidence = actual_ride_analysis.get("classification_confidence")
        reason = actual_ride_analysis.get("classification_reason")
        analysis_lines: list[str] = [
            "\nActual activity character (algorithmically derived):"
        ]
        analysis_lines.append(f"- Detected activity category: {category}")
        if confidence:
            conf_line = f"- Classification confidence: {confidence}"
            if reason:
                conf_line += f" ({reason})"
            analysis_lines.append(conf_line)
        if avg_pwr:
            analysis_lines.append(f"- Average power: {avg_pwr}W")
        if intervals:
            analysis_lines.append(f"- {len(intervals)} interval block(s) detected:")
            for iv in intervals[:4]:
                dur = iv.get("duration_secs", 0) // 60
                pwr = iv.get("avg_power_w", "?")
                pct = iv.get("power_pct_ftp", "?")
                hr_note = (
                    f", avg HR {iv['avg_hr_bpm']} bpm" if iv.get("avg_hr_bpm") else ""
                )
                analysis_lines.append(f"  • {dur} min @ {pwr}W ({pct}% FTP{hr_note})")
        else:
            analysis_lines.append(
                "- No distinct interval blocks detected (steady effort)"
            )
        if confidence in ("low", "medium"):
            analysis_lines.append(
                "- NOTE: this category is not certain. Do NOT state it as fact. If it "
                "conflicts with the planned workout (e.g. planned intervals but "
                "detected a steady ride, or vice versa), tell the athlete you are "
                "unsure how to rate the ride and ask them what they actually did — "
                "e.g. whether it was a VO2max/interval session and what the intervals "
                "were — before giving your assessment."
            )
        actual_analysis_section = "\n".join(analysis_lines)

    actual_duration = feedback.get("actualDurationMinutes")
    perceived_effort = feedback.get("perceivedEffort")
    actual_duration_line = (
        f"- Duration: {actual_duration} min\n" if actual_duration else ""
    )
    perceived_effort_line = (
        f"- Perceived effort: {perceived_effort}/5 ({effort_labels.get(perceived_effort, '')})\n"
        if perceived_effort
        else ""
    )

    return (
        f"Planned workout:\n"
        f"- Type: {day.get('workoutType', 'unknown')}\n"
        f"- Title: {day.get('title', '')}\n"
        f"- Duration: {day.get('durationMinutes', '?')} min\n"
        f"- Description: {day.get('description', '')}"
        f"{planned_power}{planned_hr}\n\n"
        f"Actual workout:\n"
        f"{actual_duration_line}"
        f"{perceived_effort_line}"
        f"{actual_power}{actual_peak}{actual_hr}{notes}"
        f"{actual_analysis_section}"
        f"{delta_section}"
        f"{profile_section}\n\n"
        f"Compare what was planned against what was actually performed (type match, numbers, effort) "
        f"and give feedback."
    )


# ---------------------------------------------------------------------------
# ask_trainer classification prompts (Task 5)
# ---------------------------------------------------------------------------


def ask_trainer_classify_system() -> str:
    return (
        "You are a routing assistant for a cycling coach chatbot. "
        "Classify the athlete's question into one of the following categories:\n"
        '- "plan_query": asking about their training plan, schedule, or specific workouts\n'
        '- "workout_modification": requesting a change, swap, or skip of a workout\n'
        '- "performance_question": asking about their performance, FTP, progress, or race results\n'
        '- "science_question": asking about training physiology, nutrition, recovery science, or methodology\n'
        '- "general_coaching": general coaching advice, motivation, or strategy\n'
        "Return ONLY a valid JSON object with these fields:\n"
        '- "category": one of the categories above\n'
        '- "needs_science_rag": true when the question would benefit from cycling science research '
        "(science_question or a detailed physiology/methodology question); false for simple plan "
        "queries, workout swaps, and schedule questions"
    )


def ask_trainer_classify_user(question: str) -> str:
    return f"Classify this athlete question: {question}"


# ---------------------------------------------------------------------------
# refresh_login_summary prompts
# ---------------------------------------------------------------------------


def refresh_login_summary_system() -> str:
    """Return the system prompt for generating a login summary from existing assessment data."""
    return (
        f"{COACH_PERSONA} Generate a concise post-login training summary for an athlete.\n"
        "You will receive their existing ride insights, most recent ride feedback, overall assessment "
        "notes, and optionally their training plan. "
        "Return ONLY a valid JSON object with a single field:\n"
        '- "loginSummary": a compact dashboard coaching brief addressed directly to the athlete. '
        "The coach decides which information is important and which details are trivial. Do not force "
        "fixed categories. Write one short intro sentence, then 2-4 bullet points. Each bullet must "
        "start with a short coach-chosen label followed by a colon, for example "
        '"- Fatigue: ..." or "- Next session: ...". Include only the most useful takeaways from '
        "recent activity, fitness signals, plan alignment, fatigue, or next actions when they genuinely "
        "matter. Omit categories with no meaningful signal. Be specific, warm, and encouraging — "
        "reference actual numbers from the data.\n"
        "When you reference the next planned session, use the provided date context and the plan "
        "entries' weekday/dateLabel/relativeDay fields to describe WHEN it occurs relative to today "
        "(for example \"today\", \"tomorrow\", \"this Saturday\", or \"next Tuesday\"). The next "
        "session is often days away — never assume it is today. Anchor every relative day to the "
        "date context; never compute a weekday from memory.\n"
        "Some activities cannot be reliably auto-classified (missing/unusable data). When the "
        "input flags a ride's classification as unknown or low/medium confidence, treat its "
        "workout type as UNCONFIRMED: do not state or imply a specific session type, training "
        "zone, or intensity for it as fact (e.g. do not call it a 'tempo ride' or 'Zone 3 "
        "effort'). Say the automatic detection was unsure, and stop there. Do NOT ask the "
        "athlete what they did in this summary — the question is put to them on the activity "
        "itself, where they can answer it, and repeating it here only asks again in a place "
        "where there is nothing to answer with.\n"
        "Not every activity is a bike ride. When the input names the activity's sport as a "
        "strength, running, yoga, hiking or other non-cycling session, that is a fact and not an "
        "unsure classification: describe it as what it was, never call it a ride, and never ask "
        "about intervals, power targets or training zones for it.\n"
        "Cite only figures the input actually gives you for an activity. A figure that is not "
        "listed was not measured — absent is not zero, and never say or imply that an average "
        "power or TSS was logged when none is given."
    )


def refresh_login_summary_user(
    ride_insights: str | None,
    last_ride_feedback: str | None,
    notes: str | None,
    estimated_ftp: int | None,
    training_plan: list[dict] | None,
    feel_legs: str | None = None,
    date_context: str | None = None,
    latest_ride_purpose: str | None = None,
    latest_ride_confidence: str | None = None,
    latest_ride_reason: str | None = None,
    latest_ride_sport_type: str | None = None,
    latest_ride_avg_power_w: int | None = None,
    latest_ride_tss: float | None = None,
) -> str:
    """Build the user message for login summary generation from existing assessment data."""
    parts: list[str] = []
    if date_context:
        parts.append(date_context)
    if estimated_ftp:
        parts.append(f"Current estimated FTP: {estimated_ftp} W")

    # Two different states used to share one instruction. "We could not work out
    # what this ride was" is an open question worth putting to the athlete; "this
    # was never a bike ride" is not — and asking the second as if it were the
    # first is how a strength session got asked what its intervals were (#578).
    _conf = (latest_ride_confidence or "").lower()
    _family = activity_family(latest_ride_sport_type)
    _is_non_cycling = bool(latest_ride_sport_type) and _family not in (
        CYCLING_FAMILY,
        UNREADABLE_FAMILY,
    )

    if _is_non_cycling:
        parts.append(
            f"The most recent activity was a {_family} session (sport type: "
            f"{latest_ride_sport_type}), not a bike ride. That is what the provider "
            "recorded, so it is a fact and not an unsure classification. Describe it "
            "as what it was; never call it a ride, and never ask about intervals, "
            "training zones, power targets or pacing for it."
        )
    elif _conf == ATHLETE_STATED_CONFIDENCE:
        # The athlete answered the question on the activity card. That is not a
        # confident inference, it is not an inference at all — and a summary that
        # hedges about a session its own athlete just named reads as not
        # listening (#580).
        parts.append(
            f"The athlete stated what the most recent activity was: "
            f"{(latest_ride_purpose or 'unknown').replace('_', ' ')}. That is their "
            "own account of the session, so treat it as fact, use it, and never "
            "describe the type as uncertain or ask about it again."
        )
    elif latest_ride_purpose == "unknown" or _conf in ("low", "medium"):
        # Deterministic guardrail: when the most recent ride could not be
        # reliably auto-classified, tell the model outright not to assert a
        # workout type for it — otherwise it invents one from the average power
        # (e.g. NP ≈ 76 % FTP narrated as a "tempo/Zone 3" ride).
        note = (
            "IMPORTANT — the most recent activity could not be reliably auto-classified "
            f"(detected type: {latest_ride_purpose or 'unknown'}, confidence: "
            f"{_conf or 'unknown'})."
        )
        if latest_ride_reason:
            note += f" {latest_ride_reason}"
        note += (
            " Do NOT state or imply a specific session type, training zone, or intensity "
            "for it as fact. Note that the automatic detection was unsure, and leave it "
            "there. The athlete is being asked what this session was on the activity "
            "itself, where they can actually answer; asking again here would only put the "
            "question somewhere it cannot be answered (#580)."
        )
        parts.append(note)

    # The instruction above used to close with "objective numbers (average power,
    # TSS, duration) are fine to cite", which invited the model to cite figures
    # that were never recorded — the gym session was narrated as having "logged
    # an average power and TSS" while both columns were NULL. State which figures
    # exist instead of naming a category of number as safe.
    if latest_ride_sport_type or latest_ride_purpose:
        recorded: list[str] = []
        if latest_ride_avg_power_w is not None:
            recorded.append(f"average power {round(latest_ride_avg_power_w)} W")
        if latest_ride_tss is not None:
            recorded.append(f"TSS {round(float(latest_ride_tss))}")
        if recorded:
            parts.append(
                "Figures recorded for the most recent activity: "
                + ", ".join(recorded)
                + ". Cite no other measured figure for it."
            )
        else:
            parts.append(
                "No average power and no TSS were recorded for the most recent "
                "activity. Do not state or imply that either was logged — absent "
                "is not zero."
            )
    if notes:
        parts.append(f"Overall assessment notes:\n{notes}")
    if feel_legs:
        parts.append(
            "Athlete's own subjective leg-feel rating after their most recent ride: "
            f"{feel_legs}. This is the athlete's self-reported signal — trust it over "
            "any inferred effort, and do not invent a perceived-effort/RPE number they "
            "did not give."
        )
    if last_ride_feedback:
        parts.append(f"Most recent activity feedback:\n{last_ride_feedback}")
    if ride_insights:
        parts.append(f"Per-activity analysis narrative:\n{ride_insights}")
    if training_plan:
        parts.append(
            f"Current training plan (for plan alignment):\n{json.dumps(training_plan, indent=2)}"
        )
    if not parts:
        parts.append("No prior activity data available.")
    return (
        "\n\n".join(parts)
        + "\n\nGenerate a loginSummary JSON object based on the above."
    )


# ---------------------------------------------------------------------------
# Training status chip (#499)
# ---------------------------------------------------------------------------

# The chip sits on one line under the dashboard greeting, beside two other
# segments. Anything longer than this wraps and breaks the strip, so the budget
# is a hard product constraint rather than a stylistic preference.
TRAINING_STATUS_LABEL_MAX_CHARS = 22

# The dashboard colours the athlete's whole training summary from this, so the
# vocabulary has to separate "you have slipped a session" from "the block is not
# happening" — one is a nudge, the other is the coach asking to talk. Before
# #623 both were "caution" and the summary could only ever look mildly amber.
TRAINING_STATUS_TONES = ("positive", "steady", "caution", "alert")


def training_status_system() -> str:
    """System prompt for the coach-authored dashboard status chip."""
    return (
        f"{COACH_PERSONA} You are writing the short training-status badge shown on the "
        "athlete's dashboard, directly under the greeting, plus the reason behind it.\n"
        "You will receive a deterministic audit of the last 7 days: every planned "
        "session with its outcome, anything optional they declined, and any unplanned "
        "work they did. Those facts are authoritative — never contradict them, and "
        "never invent a session, a number or a reason that is not in them.\n"
        "Return ONLY a valid JSON object with exactly these fields:\n"
        f'- "label": the badge text, at most {TRAINING_STATUS_LABEL_MAX_CHARS} characters. '
        "A short noun phrase in title case, no trailing punctuation, no numbers "
        '(for example "On track", "Ahead of plan", "Easing off", "Missed two"). '
        "It must be readable on its own at a glance.\n"
        f'- "tone": exactly one of {", ".join(TRAINING_STATUS_TONES)} — "positive" when they '
        'are meeting or beating the plan, "steady" when things are simply proceeding or '
        'there is no meaningful signal, "caution" when real planned work was missed but '
        'the block is still recognisable, "alert" only when most of the planned work went '
        "undone and the plan needs revisiting rather than chasing. "
        "The athlete's whole training summary is coloured from this, so it must agree "
        'with the label — do not reach for "alert" to add urgency to a good week.\n'
        '- "rationale": two or three sentences, addressed to the athlete, explaining '
        "exactly why the badge says what it says. This is the answer they get when they "
        "ask the coach 'why?', so it must cite the concrete sessions behind it.\n"
        "Judgement rules, in order of priority:\n"
        "- Declining a session the plan itself marked optional is compliance, NOT a "
        "shortfall. Never describe it as missed or as being behind.\n"
        "- Unplanned training still counts as work done. An athlete who did more than "
        "the plan asked is not behind, whatever the completion ratio says.\n"
        "- A session still ahead of them today has not been missed. Do not judge it.\n"
        "- Only genuinely missed, non-optional past sessions justify a 'caution' tone, "
        "and only a majority of them justifies 'alert'.\n"
        "- Adherence is about executing the plan, never about the athlete's fitness or "
        "physiological capability. Never imply their fitness is lacking, and never cite "
        "model confidence or missing test data as a reason for the badge."
    )


def training_status_user(
    facts: dict,
    training_plan: list[dict] | None = None,
    timezone_name: str | None = None,
) -> str:
    """Build the user message carrying the deterministic status audit."""
    today = app_today(timezone_name=timezone_name)
    # Anchor every audited session to a weekday so the coach can say "Wednesday's
    # intervals" instead of deriving a weekday from an ISO date in its head.
    anchored = {
        **facts,
        "sessions": annotate_plan_days(facts.get("sessions"), today),
        "missed": annotate_plan_days(facts.get("missed"), today),
        "skippedOptional": annotate_plan_days(facts.get("skippedOptional"), today),
        "pending": annotate_plan_days(facts.get("pending"), today),
        "extraActivities": annotate_plan_days(facts.get("extraActivities"), today),
    }

    parts: list[str] = [app_date_context(timezone_name=timezone_name)]
    parts.append(
        "Deterministic training-status audit of the last 7 days (authoritative):\n"
        f"{json.dumps(anchored, indent=2)}"
    )
    if training_plan:
        parts.append(
            "Current training plan for context:\n"
            f"{annotated_plan_json(training_plan, timezone_name, indent=2)}"
        )
    parts.append(
        "Write the status badge JSON object (label, tone, rationale) from the audit above."
    )
    return "\n\n".join(parts)


def training_status_section(
    label: str | None, tone: str | None, rationale: str | None
) -> str:
    """The status block injected into the coach's own prompts.

    Without this the coach cannot see the badge it is being asked about and
    confabulates an explanation — the #499 bug, where the dashboard said
    "Slightly behind" and the coach attributed it to model confidence.
    """
    if not label:
        return ""
    lines = [
        "\n\nTraining-status badge currently shown on this athlete's dashboard:",
        f'- Badge text: "{label}"' + (f" (tone: {tone})" if tone else ""),
    ]
    if rationale:
        lines.append(f"- The reason it says that: {rationale}")
    lines.append(
        "This badge is YOUR assessment of plan adherence over the last 7 days, and the "
        "athlete can see it. If they ask what it means or why it says that, explain it "
        "from the reason above — do not deny it, do not guess at a different cause, and "
        "do not attribute it to their fitness, to model confidence or to missing test "
        "data. It reflects planned sessions executed, nothing else."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Ride-metrics context section
# ---------------------------------------------------------------------------


_MATCH_LINKAGE_WORDING = {
    "auto_matched": "linked to a planned session automatically",
    "manual_matched": "linked to a planned session by the athlete",
    "ambiguous": "linked to a planned session, but which one is uncertain",
}


def plan_linkage_phrase(status: str | None) -> str | None:
    """How a ride's *linkage* to the plan is described to the coach.

    Deliberately not the bare enum value. The coach read "plan match:auto_matched"
    as a verdict on how the session went and told the athlete that a ride the
    dashboard had badged "Needs work" had "logged as a successful match" (#551).
    This field says only that a planned session was found for the ride — never
    how well it was executed. That is the badge below, and only the badge.
    """
    if not status or status == "unmatched":
        return None
    return _MATCH_LINKAGE_WORDING.get(status, status)


def compliance_badge_phrase(metric) -> str | None:
    """The compliance badge the athlete sees on this activity card, if any.

    An explicit ``label_override`` — set by the matcher for extra activities, or
    by the coach correcting a badge — outranks the computed one, exactly as the
    dashboard renders it.
    """
    override = getattr(metric, "label_override", None)
    if override:
        return f'"{override}" (set explicitly, overrides the computed badge)'
    return plan_compliance.describe_badge(
        getattr(metric, "match_score", None),
        getattr(metric, "match_label", None),
        getattr(metric, "matched_plan_snapshot", None),
    )



def ride_metrics_context_section(
    metrics: list,
    timezone_name: str | None = None,
    *,
    prose_window: int | None = None,
) -> str:
    """Build a compact structured-text block from a list of RideMetric ORM objects.

    Designed to fit into any LLM prompt without bloating the token count.
    Most recent activities appear first.  Returns an empty string when *metrics* is empty.

    Example output line:
        2026-04-18 | threshold_intervals | TSS 98 | NP 268W | CTL 62.3 | ATL 71.4 | TSB -9.1 | "4×8 min @ FTP"
          Coach: "Good effort, slightly over target power in intervals 3-4."
          User: "Legs felt heavy but pushed through." [consider asking for feedback]

    *prose_window* limits how far back the coach's **own** past notes are
    replayed.  Beyond the newest *prose_window* rides only the metrics line
    survives — the numbers a trend question is actually answered from — while the
    ``Coach:`` note, the classification rationale and the planned-workout title
    are dropped.  Measured against production, coach notes alone were 2,278 of
    the section's 5,063 tokens across 30 rides, roughly 104 per ride, re-read on
    every single message (#513).  The athlete's own words are deliberately *not*
    windowed: they are a fraction of the cost and they are the one thing here
    the coach cannot reconstruct from data.  ``None`` keeps every ride in full,
    which is what the analysis paths want.
    """
    if not metrics:
        return ""

    lines: list[str] = ["Recent activity history (newest first):"]
    any_badge = False
    any_estimated_load = False
    for index, m in enumerate(metrics):
        prose = prose_window is None or index < prose_window
        parts: list[str] = []

        # Date
        parts.append(str(getattr(m, "activity_date", "??")))

        # Activity type and purpose
        sport = getattr(m, "sport_type", None) or "activity"
        purpose = getattr(m, "ride_purpose", None)
        parts.append(f"sport:{sport}")
        if purpose:
            parts.append(str(purpose))

        # Classification confidence and reason
        confidence = getattr(m, "classification_confidence", None)
        reason = getattr(m, "classification_reason", None)
        if confidence:
            parts.append(f"conf:{confidence}")

        # Training load, never without where it came from: a session with no
        # power meter now carries a derived load rather than a silent zero, and
        # a coach that cannot tell the two apart will report rising cycling
        # form on the back of gym work (#579).
        load_source = getattr(m, "tss_source", None)
        load_text = format_load(getattr(m, "tss", None), load_source)
        if load_text is not None:
            parts.append(load_text)
            if load_source is not None and load_source not in MEASURED_LOAD_SOURCES:
                any_estimated_load = True

        duration_seconds = getattr(m, "duration_seconds", None)
        if duration_seconds:
            duration_minutes = max(1, round(duration_seconds / 60))
            parts.append(f"duration {duration_minutes} min")

        weather_temp = getattr(m, "weather_temperature_c", None)
        weather_condition = getattr(m, "weather_condition", None)
        if weather_temp is not None:
            weather = f"weather {round(weather_temp)}C"
            if weather_condition:
                weather += f" {str(weather_condition).replace('_', ' ')}"
            parts.append(weather)

        # Normalised power
        np_val = getattr(m, "normalized_power_w", None)
        if np_val is not None:
            parts.append(f"NP {np_val}W")

        # CTL / ATL / TSB
        ctl = getattr(m, "ctl_after", None)
        atl = getattr(m, "atl_after", None)
        tsb = getattr(m, "tsb_after", None)
        if ctl is not None:
            parts.append(f"CTL {round(ctl, 1)}")
        if atl is not None:
            parts.append(f"ATL {round(atl, 1)}")
        if tsb is not None:
            parts.append(f"TSB {round(tsb, 1)}")

        # Rule-based summary
        summary = getattr(m, "summary", None)
        if summary:
            parts.append(f'"{summary}"')

        match_status = getattr(m, "plan_match_status", None)
        matched_date = getattr(m, "matched_plan_date", None)
        matched_snapshot = getattr(m, "matched_plan_snapshot", None)
        linkage = plan_linkage_phrase(match_status)
        if linkage:
            parts.append(f"plan linkage:{linkage}")
            if matched_date:
                parts.append(f"planned {matched_date}")
        badge = compliance_badge_phrase(m)
        if badge:
            parts.append(f"badge:{badge}")
            any_badge = True

        line = " | ".join(parts)
        lines.append(f"  {line}")

        if prose and isinstance(matched_snapshot, dict):
            title = matched_snapshot.get("title") or matched_snapshot.get("workoutType")
            duration = matched_snapshot.get("durationMinutes")
            if title:
                duration_part = f", {duration} min" if duration else ""
                lines.append(f"    Planned workout: {title}{duration_part}")

        # Classification reason — only shown when confidence is not high
        if prose and reason and confidence != "high":
            lines.append(f"    [classification: {reason}]")

        # Coach note
        coach_note = getattr(m, "coach_note", None)
        if prose and coach_note:
            lines.append(f'    Coach: "{coach_note}"')

        # Quick leg-freshness the athlete tapped on the dashboard (may be unset)
        feel_legs = getattr(m, "feel_legs", None)
        if feel_legs:
            lines.append(f"    Athlete legs: {feel_legs}")

        # User note — flag if missing and the ride was recent (last 3 days)
        user_note = getattr(m, "user_note", None)
        activity_date_str = str(getattr(m, "activity_date", ""))
        try:
            from datetime import date as _date

            days_ago = (
                app_today(timezone_name=timezone_name)
                - _date.fromisoformat(activity_date_str)
            ).days
        except ValueError:
            days_ago = 99
        if user_note:
            lines.append(f'    Athlete: "{user_note}"')
        elif not feel_legs and days_ago <= 3:
            lines.append(
                "    [no athlete feedback — consider asking how this ride felt]"
            )

    # Only worth its tokens once there is a badge on screen to be asked about.
    if any_badge:
        lines.append(BADGE_GROUNDING_RULE)
    # Likewise: only spend the legend when an estimated load is actually on the
    # page. An athlete whose rides all carry power never sees these lines.
    if any_estimated_load:
        lines.append(ESTIMATED_LOAD_RULE)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# batch_review_rides prompts
# ---------------------------------------------------------------------------


def batch_review_system() -> str:
    return (
        f"{COACH_PERSONA} Review a batch of newly imported rides that the athlete has not yet "
        "received feedback on. Treat these rides as a small training block, not as isolated events.\n"
        "Your review must cover:\n"
        "1. What changed across the rides (e.g. intensity progression, load variation).\n"
        "2. Whether each ride matched the training plan (if a plan was provided).\n"
        "3. Flag any ride that was short, ambiguous, over-paced, or especially strong — "
        "and for ambiguous/short rides, ask one concise follow-up question rather than over-interpreting.\n"
        "4. The combined fatigue/load impact of the batch (CTL/ATL/TSB trends if available).\n"
        "5. What the next planned session should focus on given the batch.\n"
        f"{PLAN_TIMING_GUIDANCE}\n"
        "Keep the response concise and warm: 4-8 sentences or a short structured paragraph. "
        "Do not address the athlete by name in most messages; use it at most once every several exchanges and never as a sentence opener.\n"
        "Return ONLY a valid JSON object with exactly one field:\n"
        '- "review": your coaching response as a string'
    )


def batch_review_user(
    rides: list,
    profile: dict | None = None,
    training_plan: list[dict] | None = None,
    timezone_name: str | None = None,
) -> str:
    """Build the user message for a batch ride review.

    *rides* is a list of RideMetric ORM objects (or duck-typed equivalents).
    """
    date_context = app_date_context(timezone_name=timezone_name)
    profile_section = f"\nAthlete profile: {json.dumps(profile)}" if profile else ""

    plan_section = ""
    if training_plan:
        plan_section = (
            "\nTraining plan (relevant days, with weekday/dateLabel/relativeDay anchors): "
            f"{annotated_plan_json(training_plan, timezone_name)}"
        )

    rides_lines: list[str] = ["Rides to review (oldest first):"]
    for m in rides:
        parts: list[str] = []
        parts.append(f"Date: {getattr(m, 'activity_date', '?')}")
        purpose = getattr(m, "ride_purpose", None) or getattr(m, "sport_type", "ride")
        parts.append(f"Type: {purpose}")
        confidence = getattr(m, "classification_confidence", None)
        reason = getattr(m, "classification_reason", None)
        if confidence:
            parts.append(f"Confidence: {confidence}")
        duration = getattr(m, "duration_seconds", None)
        if duration:
            parts.append(f"Duration: {round(duration / 60)} min")
        load_text = format_load_field(
            getattr(m, "tss", None), getattr(m, "tss_source", None)
        )
        if load_text is not None:
            parts.append(load_text)
        np_val = getattr(m, "normalized_power_w", None)
        if np_val is not None:
            parts.append(f"NP: {np_val}W")
        avg_pwr = getattr(m, "avg_power_w", None)
        if avg_pwr is not None and np_val is None:
            parts.append(f"Avg power: {avg_pwr}W")
        weather_temp = getattr(m, "weather_temperature_c", None)
        weather_condition = getattr(m, "weather_condition", None)
        if weather_temp is not None:
            weather = f"Weather: {round(weather_temp)}C"
            if weather_condition:
                weather += f" {str(weather_condition).replace('_', ' ')}"
            parts.append(weather)
        ctl = getattr(m, "ctl_after", None)
        atl = getattr(m, "atl_after", None)
        tsb = getattr(m, "tsb_after", None)
        if ctl is not None:
            parts.append(f"CTL: {round(ctl, 1)}")
        if atl is not None:
            parts.append(f"ATL: {round(atl, 1)}")
        if tsb is not None:
            parts.append(f"TSB: {round(tsb, 1)}")
        coach_note = getattr(m, "coach_note", None)
        if coach_note:
            parts.append(f'Coach note: "{coach_note}"')
        user_note = getattr(m, "user_note", None)
        if user_note:
            parts.append(f'Athlete note: "{user_note}"')
        if reason and confidence != "high":
            parts.append(f"[classification note: {reason}]")
        match_status = getattr(m, "plan_match_status", None)
        matched_snapshot = getattr(m, "matched_plan_snapshot", None)
        linkage = plan_linkage_phrase(match_status)
        if linkage:
            parts.append(f"Plan linkage: {linkage}")
        badge = compliance_badge_phrase(m)
        if badge:
            parts.append(f"Compliance badge: {badge}")
        if isinstance(matched_snapshot, dict):
            parts.append(
                "Matched planned workout: "
                f"{matched_snapshot.get('title') or matched_snapshot.get('workoutType', 'planned workout')}"
            )
        rides_lines.append("  - " + " | ".join(parts))

    rides_section = "\n".join(rides_lines)

    return (
        f"{date_context}"
        f"{profile_section}"
        f"{plan_section}\n\n"
        f"{rides_section}\n\n"
        "Please review these rides as a training block and give feedback."
    )


# ---------------------------------------------------------------------------
# next_ride_recommendation prompts (Task 6)
# ---------------------------------------------------------------------------


def next_ride_recommendation_system() -> str:
    return (
        f"{COACH_PERSONA} Give a concrete recommendation for the athlete's next training session "
        "based on their recent ride(s) and subjective feedback.\n\n"
        "Use two internal decision layers:\n"
        "1. Physiology layer: CTL, ATL, TSB, HRV/sleep if available, recent load, ride feedback, "
        "and the planned training stimulus.\n"
        "2. Athlete-context layer: motivation, rest tolerance, tendency to overdo it, social needs, "
        "mood, adherence pattern, structured athlete context, and evidence-backed memory facts.\n"
        "Hard athlete availability constraints from coach memory, athlete context, or recent "
        "conversation are binding: never schedule or recommend training on a constrained date "
        "or weekday, even when it would be physiologically optimal. Move the stimulus to the "
        "best available day or ask one concise clarifying question if no feasible slot is clear.\n"
        "If the physiology layer permits more than one sensible option, choose the option that better "
        "fits the athlete-context layer. For example, choose recovery or an easy/social ride when "
        "metrics permit intensity but personal context suggests the athlete needs restraint, motivation, "
        "or a lower-pressure session.\n"
        "When two materially different recommendations remain plausible because personal context is missing, "
        "ask exactly one short targeted learning question before committing. Use questions like: "
        "'Do you need training stimulus today, or mainly head-clearing?', 'Are you restless because you feel fresh, "
        "or because you are worried about losing fitness?', or 'Would a social ride help you more than another "
        "structured session today?'. Do not ask when physiology, recent feedback, or known athlete context already "
        "makes the recommendation clear.\n"
        "Keep the final response concise and natural; do not expose layer labels unless useful.\n\n"
        "You MUST choose one of the following recommendation types and explain why:\n"
        "- 'keep_as_planned': the next session should proceed exactly as scheduled\n"
        "- 'easier': do the same type of session but reduce intensity or duration\n"
        "- 'recovery': replace with a recovery spin or full rest day\n"
        "- 'move_intensity': postpone any high-intensity work to a later session\n\n"
        "Base your decision on: recent TSS, CTL/ATL/TSB, subjective RPE, leg feel, "
        "and how recent rides compared to the plan.\n\n"
        f"{PLAN_TIMING_GUIDANCE}\n\n"
        f"{hard_session_spacing_rules()}\n\n"
        "Return ONLY a valid JSON object with these fields:\n"
        '- "response": a warm, personal 2-4 sentence coaching message that explains '
        "what you recommend and why\n"
        '- "next_session_recommendation": a brief one-sentence summary of the recommendation '
        "(e.g. 'Take tomorrow as a full rest day — your TSB is deeply negative and legs felt heavy.')\n"
        '- "recommendation_type": one of "keep_as_planned", "easier", "recovery", "move_intensity"\n'
        '- "planUpdates": a JSON array of plan-day updates — include ONLY when the next session '
        "should actually change; omit or use null if keeping as planned. "
        "Each update has: date (YYYY-MM-DD), workoutType, title, description, durationMinutes. "
        "Only update days that are TODAY or in the future."
    )


def next_ride_recommendation_user(
    rides: list,
    plan: list[dict],
    profile: dict | None = None,
    rider_assessment: dict | None = None,
    coach_memory: str | None = None,
    athlete_context: dict | None = None,
    athlete_memory_facts: list[dict] | None = None,
    athlete_model: dict | None = None,
    motivation_model: dict | None = None,
    performance_recommendation: dict | None = None,
    ctl: float | None = None,
    atl: float | None = None,
    tsb: float | None = None,
    timezone_name: str | None = None,
) -> str:
    """Build the user message for a next-ride recommendation.

    *rides* is a list of RideMetric ORM objects (or duck-typed dicts) with recent
    activity data including user_note (subjective feedback).
    """
    today = app_today_iso(timezone_name=timezone_name)
    today_date = app_today(timezone_name=timezone_name)

    parts: list[str] = [app_date_context(timezone_name=timezone_name)]
    athlete_context_parts: list[str] = []
    physiology_parts: list[str] = []

    if profile:
        athlete_context_parts.append(f"Athlete profile: {json.dumps(profile)}")

    if rider_assessment:
        athlete_context_parts.append(
            f"Rider assessment: {json.dumps(rider_assessment)}"
        )

    structured_context = athlete_context_section(athlete_context).strip()
    if structured_context:
        athlete_context_parts.append(structured_context)

    motivation = motivation_model_section(motivation_model).strip()
    if motivation:
        athlete_context_parts.append(motivation)

    structured_model = athlete_model_section(athlete_model).strip()
    if structured_model:
        athlete_context_parts.append(structured_model)

    memory_facts = athlete_memory_facts_section(athlete_memory_facts).strip()
    if memory_facts:
        athlete_context_parts.append(memory_facts)

    if coach_memory:
        trimmed = coach_memory[-800:]
        athlete_context_parts.append(f"Coach notes about this athlete:\n{trimmed}")

    # Current training load
    load_parts: list[str] = []
    if ctl is not None:
        load_parts.append(f"CTL (fitness): {round(ctl, 1)}")
    if atl is not None:
        load_parts.append(f"ATL (fatigue): {round(atl, 1)}")
    if tsb is not None:
        load_parts.append(f"TSB (form): {round(tsb, 1)}")
    if load_parts:
        physiology_parts.append("Current training load: " + " | ".join(load_parts))

    roi_section = athlete_performance_roi_section(performance_recommendation).strip()
    if roi_section:
        physiology_parts.append(roi_section)

    # Recent rides with feedback
    if rides:
        rides_lines: list[str] = ["Recent ride(s) to review (newest first):"]
        for m in rides:
            ride_parts: list[str] = []
            ride_parts.append(f"Date: {getattr(m, 'activity_date', '?')}")
            purpose = getattr(m, "ride_purpose", None) or getattr(
                m, "sport_type", "ride"
            )
            ride_parts.append(f"Type: {purpose}")
            duration = getattr(m, "duration_seconds", None)
            if duration:
                ride_parts.append(f"Duration: {round(duration / 60)} min")
            load_text = format_load_field(
                getattr(m, "tss", None), getattr(m, "tss_source", None)
            )
            if load_text is not None:
                ride_parts.append(load_text)
            np_val = getattr(m, "normalized_power_w", None)
            if np_val is not None:
                ride_parts.append(f"NP: {np_val}W")
            m_ctl = getattr(m, "ctl_after", None)
            m_atl = getattr(m, "atl_after", None)
            m_tsb = getattr(m, "tsb_after", None)
            if m_ctl is not None:
                ride_parts.append(f"CTL after: {round(m_ctl, 1)}")
            if m_atl is not None:
                ride_parts.append(f"ATL after: {round(m_atl, 1)}")
            if m_tsb is not None:
                ride_parts.append(f"TSB after: {round(m_tsb, 1)}")
            coach_note = getattr(m, "coach_note", None)
            if coach_note:
                ride_parts.append(f'Coach note: "{coach_note}"')
            user_note = getattr(m, "user_note", None)
            if user_note:
                ride_parts.append(f'Athlete feedback: "{user_note}"')
            match_status = getattr(m, "plan_match_status", None)
            matched_snapshot = getattr(m, "matched_plan_snapshot", None)
            linkage = plan_linkage_phrase(match_status)
            if linkage:
                ride_parts.append(f"Plan linkage: {linkage}")
            badge = compliance_badge_phrase(m)
            if badge:
                ride_parts.append(f"Compliance badge: {badge}")
            if isinstance(matched_snapshot, dict):
                ride_parts.append(
                    "Matched planned workout: "
                    f"{matched_snapshot.get('title') or matched_snapshot.get('workoutType', 'planned workout')}"
                )
            rides_lines.append("  - " + " | ".join(ride_parts))
        physiology_parts.append("\n".join(rides_lines))
    else:
        physiology_parts.append("No recent rides available.")

    # Next planned session(s)
    upcoming = [
        d for d in plan if d.get("date", "") >= today and not d.get("completed")
    ][:3]
    if upcoming:
        next_session = upcoming[0]
        physiology_parts.append(
            f"Next planned session — {_plan_day_when(next_session, today_date)}: "
            f"{next_session.get('title', 'Unknown')} — {next_session.get('workoutType', '?')}, "
            f"{next_session.get('durationMinutes', '?')} min. "
            f"Description: {next_session.get('description', '')}"
        )
        if len(upcoming) > 1:
            physiology_parts.append(
                "Following sessions: "
                + ", ".join(
                    f"{_plan_day_when(d, today_date)} {d.get('title', d.get('workoutType', '?'))}"
                    for d in upcoming[1:]
                )
            )
    else:
        physiology_parts.append("No upcoming sessions in the training plan.")

    parts.append("Physiology layer:\n" + "\n\n".join(physiology_parts))
    if athlete_context_parts:
        parts.append("Athlete-context layer:\n" + "\n\n".join(athlete_context_parts))
    else:
        parts.append("Athlete-context layer:\nNo durable athlete context provided.")

    parts.append(
        "\nBased on the recent ride(s) and feedback, give a concrete recommendation "
        "for what the athlete should do in their next training session."
    )

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Process-pending-feedbacks prompts
# ---------------------------------------------------------------------------


def process_pending_feedbacks_system() -> str:
    """Return the system prompt for generating a training summary from multiple ride feedbacks."""
    return (
        f"{COACH_PERSONA} Generate an updated training summary after receiving fresh athlete "
        "feedback or new activity context for one or more recent activities.\n"
        "You will receive the activities to summarize in chronological order with names, dates, "
        "types, load metrics, and optional athlete notes.\n"
        "The listed activities are the authoritative basis for the summary. Do not summarize an "
        "older activity from assessment notes when it is not in the listed activities. If assessment "
        "notes conflict with the listed activities, prefer the listed activity data.\n"
        "The first bullet must summarize the latest listed activity, not the longest or most "
        "notable older activity.\n"
        "Return ONLY a valid JSON object with a single field:\n"
        '- "loginSummary": a compact dashboard coaching brief addressed directly to the athlete. '
        "The coach decides which information is important and which details are trivial. Do not force "
        "fixed categories. Write one short intro sentence, then 2-4 bullet points. Each bullet must "
        "start with a short coach-chosen label followed by a colon, for example "
        '"- Fatigue: ..." or "- Next session: ...". Include only the most useful takeaways from '
        "the athlete's notes, ride load, plan alignment, fatigue, or next actions when they genuinely "
        "matter. Omit categories with no meaningful signal. Be specific, warm, and encouraging — "
        f"reference actual numbers from the data. {PLAN_TIMING_GUIDANCE}"
    )


def process_pending_feedbacks_user(
    rides: list,
    assessment: dict | None = None,
    training_plan: list[dict] | None = None,
    timezone_name: str | None = None,
) -> str:
    """Build the user message for generating a summary from multiple ride feedbacks.

    *rides* is a list of RideMetric ORM objects sorted oldest-first.
    """
    today = app_today_iso(timezone_name=timezone_name)
    today_date = app_today(timezone_name=timezone_name)
    parts: list[str] = [app_date_context(timezone_name=timezone_name)]

    if assessment:
        ftp = assessment.get("estimatedFtp") or assessment.get("estimated_ftp")
        if ftp:
            parts.append(f"Athlete estimated FTP: {ftp} W")

    if rides:
        latest = rides[-1]
        latest_name = getattr(latest, "activity_name", None) or "Unnamed activity"
        latest_date = getattr(latest, "activity_date", "?")
        latest_type = getattr(latest, "ride_purpose", None) or getattr(
            latest, "sport_type", "activity"
        )
        latest_duration = getattr(latest, "duration_seconds", None)
        latest_parts = [
            f"Name: {latest_name}",
            f"Date: {latest_date}",
            f"Type: {latest_type}",
        ]
        if latest_duration:
            latest_parts.append(f"Duration: {round(latest_duration / 60)} min")
        parts.append(
            "Latest listed activity (anchor the first summary bullet on this activity): "
            + " | ".join(latest_parts)
        )
        rides_lines: list[str] = [
            "Activities to summarize (chronological order; authoritative):"
        ]
        for m in rides:
            ride_parts: list[str] = []
            name = getattr(m, "activity_name", None)
            if name:
                ride_parts.append(f"Name: {name}")
            ride_parts.append(f"Date: {getattr(m, 'activity_date', '?')}")
            purpose = getattr(m, "ride_purpose", None) or getattr(
                m, "sport_type", "ride"
            )
            ride_parts.append(f"Type: {purpose}")
            duration = getattr(m, "duration_seconds", None)
            if duration:
                ride_parts.append(f"Duration: {round(duration / 60)} min")
            load_text = format_load_field(
                getattr(m, "tss", None), getattr(m, "tss_source", None)
            )
            if load_text is not None:
                ride_parts.append(load_text)
            np_val = getattr(m, "normalized_power_w", None)
            if np_val is not None:
                ride_parts.append(f"NP: {np_val}W")
            ctl = getattr(m, "ctl_after", None)
            atl = getattr(m, "atl_after", None)
            tsb = getattr(m, "tsb_after", None)
            if ctl is not None:
                ride_parts.append(f"CTL after: {round(float(ctl), 1)}")
            if atl is not None:
                ride_parts.append(f"ATL after: {round(float(atl), 1)}")
            if tsb is not None:
                ride_parts.append(f"TSB after: {round(float(tsb), 1)}")
            user_note = getattr(m, "user_note", None)
            if user_note:
                ride_parts.append(f'Athlete feedback: "{user_note}"')
            coach_note = getattr(m, "coach_note", None)
            if coach_note:
                ride_parts.append(f'Previous coach note: "{coach_note}"')
            match_status = getattr(m, "plan_match_status", None)
            matched_snapshot = getattr(m, "matched_plan_snapshot", None)
            linkage = plan_linkage_phrase(match_status)
            if linkage:
                ride_parts.append(f"Plan linkage: {linkage}")
            badge = compliance_badge_phrase(m)
            if badge:
                ride_parts.append(f"Compliance badge: {badge}")
            if isinstance(matched_snapshot, dict):
                ride_parts.append(
                    "Matched planned workout: "
                    f"{matched_snapshot.get('title') or matched_snapshot.get('workoutType', 'planned workout')}"
                )
            rides_lines.append("  - " + " | ".join(ride_parts))
        parts.append("\n".join(rides_lines))
    else:
        parts.append("No activity data available.")

    if training_plan:
        upcoming = [
            d
            for d in training_plan
            if d.get("date", "") >= today and not d.get("completed")
        ][:3]
        if upcoming:
            parts.append(
                "Upcoming planned sessions: "
                + ", ".join(
                    f"{_plan_day_when(d, today_date)} {d.get('title', d.get('workoutType', '?'))}"
                    for d in upcoming
                )
            )

    parts.append(
        "\nGenerate an updated loginSummary JSON that reflects the listed activities above "
        "and gives forward-looking coaching guidance. Ignore older free-form assessment notes; "
        "the first bullet must mention the latest listed activity by name."
    )
    return "\n\n".join(parts)
