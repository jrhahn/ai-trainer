"""Prompt construction functions for the AI service.

All functions are pure: they accept typed parameters and return strings.
No LLM client logic, algorithmic computation, or I/O here.
"""

from __future__ import annotations

import json

COACH_PERSONA = (
    "You are a professional cycling coach with extensive experience in "
    "competitive road, track, and endurance cycling. "
    "Always address the athlete directly using 'you' — for example, "
    "'You have excellent aerobic endurance' not 'The athlete has excellent aerobic endurance'. "
    "Your coaching philosophy: long-term athletic development always overrules short-term gains. "
    "Never sacrifice recovery, health, or sustainable progression for quick wins. "
    "When in doubt, prioritise the athlete's long-term progress over immediate performance."
)

RUNNING_COACH_PERSONA = (
    "You are a professional running coach with extensive experience in "
    "competitive road, track, and trail running. "
    "Always address the athlete directly using 'you' — for example, "
    "'You have excellent aerobic endurance' not 'The athlete has excellent aerobic endurance'. "
    "Your coaching philosophy: long-term athletic development always overrules short-term gains. "
    "Never sacrifice recovery, health, or sustainable progression for quick wins. "
    "When in doubt, prioritise the athlete's long-term progress over immediate performance."
)

TRAINING_PLAN_PRINCIPLES = """
Training plan scheduling rules (ALWAYS follow these):
- Schedule long endurance and base rides on Saturday and Sunday.
- Keep weekday sessions short (60-90 min maximum) to fit around work.
- Do not rely on a 'weeklyHours' field; derive realistic weekly volume from the athlete's fitness level:
  * beginner: ~3-5 hours/week, no session longer than 90 min
  * intermediate: ~5-8 hours/week, weekend rides up to 2.5 h
  * advanced: ~8-12 hours/week, weekend rides up to 3.5 h
- Make intensity/volume realistic for the athlete's current fitness level and stated goal.
- For race-prep goals: taper in the final week before the race date (reduce volume by ~40%, keep intensity).
- Progressive overload: gradually increase load week-over-week, but include a recovery day after every hard session.
- Never schedule two hard days back-to-back.
- If a rider assessment (FTP/threshold HR) is available, use it to set precise power/HR targets for every workout.
"""


# ---------------------------------------------------------------------------
# analyse_strava_activities prompts
# ---------------------------------------------------------------------------


def analyse_activities_system(sport_type: str = "cycling") -> str:
    """Return the system prompt for activity analysis.

    When *sport_type* is ``"running"`` (or any non-cycling type) a running-
    specific prompt is returned that omits power-based FTP and uses HR-based
    thresholds instead.
    """
    is_running = sport_type.lower() in ("running", "run")

    if is_running:
        persona = RUNNING_COACH_PERSONA
        activity_noun = "run"
        activities_noun = "runs"
        ftp_field = (
            "- \"estimatedFTP\": always null for running (no power data)\n"
        )
        threshold_hr_field = (
            "- \"estimatedThresholdHR\": integer bpm — use the pre-computed value when provided, "
            "otherwise estimate from HR data (typically ~87% of max HR for a threshold effort); "
            "null if no HR data\n"
        )
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
    else:
        persona = COACH_PERSONA
        activity_noun = "ride"
        activities_noun = "rides"
        ftp_field = (
            "- \"estimatedFTP\": integer watts — use the pre-computed value when provided, "
            "otherwise estimate from activity summaries; null if no power data\n"
        )
        threshold_hr_field = (
            "- \"estimatedThresholdHR\": integer bpm — use the pre-computed value when provided, "
            "otherwise estimate from activity summaries; null if no HR data\n"
        )
        category_section = (
            "Ride categories:\n"
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

    return (
        f"{persona} Analyse the provided activities and return a JSON assessment.\n"
        "Return ONLY a valid JSON object with these fields "
        "(all keys double-quoted, numeric values must be plain numbers with no units):\n"
        f"{ftp_field}"
        f"{threshold_hr_field}"
        "- \"riderType\": one of \"timetrial\", \"sprinter\", \"climber\", \"allrounder\", \"endurance\"\n"
        "- \"notes\": a concise overall assessment addressed directly to the athlete using 'you'. "
        f"Mention their strengths, rider type, and key observations from their {activities_noun}. "
        f"{notes_example}\n"
        f"- \"lastRideFeedback\": a standalone 3-5 sentence coach note about the SINGLE MOST RECENT {last_ride_key} only "
        f"(the one with the latest start_date). Write it as a card the athlete reads first thing on their dashboard. "
        f"Cover: (1) what type of {last_ride_key} it was (category) and key numbers, "
        "(2) how the effort looked — "
        + ("HR response and pace consistency or drift if data available, " if is_running else "power consistency and HR response or drift if data available, ")
        + "(3) one concrete recommendation for the next training session. "
        "Be warm, personal, and specific — use their actual numbers.\n"
        f"- \"rideInsights\": a per-{activity_noun} narrative addressed to the athlete. "
        f"For each {activity_noun}: state its category, "
        "comment on the effort quality (HR drift if data available), and give one "
        "concrete takeaway. Also include 1-2 specific recommendations for the athlete's next training "
        "session based on what you observed. Be empathetic and personal — reference their specific numbers.\n"
        "- \"loginSummary\": a structured 4-part coach summary of all the provided activities for display "
        "on the athlete's dashboard immediately after login. Write it as flowing prose (not bullet points), "
        "addressed directly to the athlete. Include exactly these four parts in order:\n"
        "  (1) WHAT YOU DID: Briefly summarise the volume, types, and key numbers from the recent "
        f"{activities_noun} — total time, any highlights, what was strong. "
        "Also flag what could be improved.\n"
        "  (2) FTP & FITNESS INSIGHTS: Comment on any FTP changes or trends visible across the "
        f"{activities_noun}. Is volume too low, too high, or about right? Any signs of fatigue or "
        "fitness gains?\n"
        "  (3) PLAN ALIGNMENT: If a training plan is provided, assess how well the recent "
        f"{activities_noun} matched the planned sessions — were targets hit, sessions skipped, or "
        "intensity off? If no plan is provided, note that no plan context is available.\n"
        "  (4) CONCLUSIONS: What does this mean for upcoming training? Give 1-2 concrete, actionable "
        "recommendations the athlete should follow in their next sessions.\n"
        "Keep the total loginSummary to 5-8 sentences. Be specific, warm, and encouraging.\n"
        "- \"planUpdates\": optional array of training day updates for the upcoming plan based on what "
        f"you observed in the {activities_noun}. Only include updates that are genuinely warranted (e.g. add recovery "
        "if athlete shows fatigue/HR drift, increase intensity if athlete is clearly above their current "
        "targets). Each update: {\"date\": \"<ISO date>\", \"workoutType\": \"<type>\", \"title\": "
        "\"<string>\", \"description\": \"<string>\", \"durationMinutes\": <int>}. "
        "If no updates are needed, omit this field or set it to [].\n\n"
        f"{category_section}"
        f"{rider_type_section}"
    )


def analyse_activities_user(
    activities: list[dict],
    computed_section: str,
    ride_analyses_section: str,
    sport_type: str = "cycling",
    training_plan: list[dict] | None = None,
) -> str:
    is_running = sport_type.lower() in ("running", "run")
    activities_noun = "runs" if is_running else "Strava rides"
    ftp_note = (
        "When pre-computed threshold HR values are given, "
        "use them verbatim for estimatedThresholdHR. Set estimatedFTP to null. "
    ) if is_running else (
        "When pre-computed FTP/threshold HR values are given, "
        "use them verbatim for estimatedFTP and estimatedThresholdHR. "
    )
    plan_section = ""
    if training_plan:
        plan_section = (
            f"\n\nCurrent training plan (use for plan alignment in loginSummary):\n"
            f"{json.dumps(training_plan, indent=2)}"
        )
    return (
        f"Last {len(activities)} {activities_noun}:\n{json.dumps(activities, indent=2)}"
        f"{computed_section}"
        f"{ride_analyses_section}"
        f"{plan_section}\n\n"
        f"Assess my fitness. {ftp_note}"
        "Use the per-activity analyses above to write accurate rideInsights and appropriate planUpdates."
    )


def analyse_activities_computed_section(
    computed_ftp: int | None,
    computed_threshold_hr: int | None,
    max_heart_rate: int | None,
    computed_hr_zones: dict | None,
) -> str:
    """Build the contextual block describing algorithmically derived metrics.

    Returns an empty string when no metrics are available.
    """
    section = ""
    if computed_ftp is not None:
        section += (
            f"\nAlgorithmically estimated FTP from stream data: {computed_ftp} W "
            "(95 % of best 20-min average power)"
        )
    if computed_threshold_hr is not None:
        section += (
            f"\nAlgorithmically estimated threshold HR: {computed_threshold_hr} bpm "
            "(average HR during best 20-min power effort)"
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


def generate_plan_system() -> str:
    return (
        f"{COACH_PERSONA} Generate a 14-day training plan as JSON.\n"
        "Return ONLY a valid JSON object with a \"plan\" array of training days. "
        "All keys must be double-quoted. All numeric fields must be plain numbers with no units.\n"
        "Each day must have: \"date\" (ISO date string starting from today), "
        "\"workoutType\" (one of: \"rest\",\"endurance\",\"intervals\",\"tempo\",\"race\","
        "\"recovery\",\"strength\"), \"title\" (string), "
        "\"description\" (a 2-4 sentence summary of the session using the athlete's actual FTP and "
        "threshold HR values to state exact power/HR targets — never write percentages alone, always "
        "translate them to absolute numbers, e.g. 'Ride for 90 min at 195–220 W (Zone 2, 75–85% of "
        "your 260 W FTP). Keep HR under 148 bpm. The goal is fat oxidation and aerobic base building "
        "— you should be able to hold a conversation throughout.'), "
        "\"durationMinutes\" (integer), "
        "\"workoutPurpose\" (1-2 sentences describing the physiological goal of this session and why "
        "it is placed here in the plan — e.g. 'This tempo block raises your lactate threshold by "
        "training your body to clear lactate more efficiently. It follows yesterday's recovery ride "
        "to take advantage of residual fatigue adaptation.'), "
        "\"keyFocusPoints\" (array of 3-5 short coaching-cue strings, each beginning with an action "
        "verb — e.g. [\"Keep cadence between 88-95 rpm throughout\", \"HR must stay below 158 bpm "
        "(Zone 3); back off if it creeps higher\", \"Breathe rhythmically — aim for a 3-in/2-out "
        "pattern on climbs\"]).\n"
        "Optional fields: \"targetPower\" (object with \"low\" and \"high\" integer fields in watts), "
        "\"targetHeartRate\" (object with \"low\" and \"high\" integer fields in bpm), "
        "\"intervals\" (array of objects with \"duration\" (integer seconds), "
        "\"power\" (integer watts), \"rest\" (integer seconds)).\n"
        f"{TRAINING_PLAN_PRINCIPLES}"
        "Workout type guidance:\n"
        "- For FTP improvement: include threshold and VO2max work\n"
        "- For race prep: include race-specific workouts and a taper week\n"
        "- For weight loss: emphasise longer aerobic sessions\n"
        "- Tailor workout types to the rider type "
        "(e.g. more sprints for sprinters, more climbs for climbers, sustained tempo for TT riders)"
    )


def generate_plan_user(profile: dict, today: str, assessment_section: str) -> str:
    return (
        f"Today's date: {today}\n"
        f"Profile: {json.dumps(profile)}{assessment_section}\n"
        "Generate a 14-day training plan starting from today that reflects both the athlete's "
        "goals and their actual fitness level from recent rides."
    )


# ---------------------------------------------------------------------------
# adapt_training_plan prompts
# ---------------------------------------------------------------------------


def adapt_plan_system() -> str:
    return (
        f"{COACH_PERSONA} Adapt the remaining training plan based on recent workout feedback.\n"
        "Return ONLY a valid JSON object with an \"updatedDays\" array. "
        "All keys must be double-quoted. All numeric fields must be plain numbers with no units. "
        "For future days keep the same date fields. "
        "For any past incomplete days (date before today), reschedule them to upcoming dates "
        "starting from today, distributing the sessions sensibly without overloading consecutive days.\n"
        "Each updated day must include all required TrainingDay fields: "
        "\"date\", \"workoutType\", \"title\", \"durationMinutes\".\n"
        "Each updated day must also include: "
        "\"description\" (a 2-4 sentence summary using the athlete's actual FTP and threshold HR to "
        "state exact power/HR targets — always translate percentages to absolute numbers), "
        "\"workoutPurpose\" (1-2 sentences on the physiological goal of the session and why it is "
        "placed here in the adapted plan), "
        "\"keyFocusPoints\" (array of 3-5 coaching-cue strings, each starting with an action verb).\n"
        f"{TRAINING_PLAN_PRINCIPLES}"
        "Use TSB to guide adaptation: TSB < −20 suggests accumulated fatigue, prioritise recovery; "
        "TSB > +10 before a key workout suggests freshness, intensity can be increased."
    )


def adapt_plan_user(
    profile: dict,
    today: str,
    recent_feedback: list[dict],
    incomplete_days: list[dict],
    rider_assessment: dict | None = None,
    training_load: dict | None = None,
    taper_days_remaining: int | None = None,
) -> str:
    assessment_section = (
        f"\nRider assessment: {json.dumps(rider_assessment)}" if rider_assessment else ""
    )
    load_section = ""
    if training_load:
        load_section = (
            f"\nTraining load (from plan): CTL={training_load.get('ctl')} "
            f"ATL={training_load.get('atl')} TSB={training_load.get('tsb')}"
        )
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
        f"Profile: {json.dumps(profile)}{assessment_section}{load_section}{taper_section}\n"
        f"Recent feedback: {json.dumps(recent_feedback)}\n"
        f"Remaining plan days: {json.dumps(incomplete_days)}\n"
        + stale_note
        + "Adapt the remaining days based on the feedback. Return the full updated days array."
    )


# ---------------------------------------------------------------------------
# ask_trainer prompts
# ---------------------------------------------------------------------------


def ask_trainer_assessment_section(rider_assessment: dict | None) -> str:
    """Build the rider-assessment section string. Returns '' when falsy."""
    if not rider_assessment:
        return ""
    rider_type = rider_assessment.get("riderType", "")
    ftp = rider_assessment.get("estimatedFTP")
    thr = rider_assessment.get("estimatedThresholdHR")
    notes = rider_assessment.get("notes", "")
    assessment_lines = [f"- Rider type: {rider_type}"]
    if ftp:
        assessment_lines.append(f"- Estimated FTP: {ftp} W")
    if thr:
        assessment_lines.append(f"- Estimated threshold HR: {thr} bpm")
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
        "or power you MUST include the full \"intervals\" array in planUpdates. "
        "The array must contain EXACTLY the requested number of objects, one per interval rep. "
        "Each object: {\"duration\": <seconds>, \"power\": <watts>, \"rest\": <seconds>}. "
        "Example — athlete asks for 4×2 min at 370 W with 3 min rest: "
        "\"intervals\": ["
        "{\"duration\": 120, \"power\": 370, \"rest\": 180}, "
        "{\"duration\": 120, \"power\": 370, \"rest\": 180}, "
        "{\"duration\": 120, \"power\": 370, \"rest\": 180}, "
        "{\"duration\": 120, \"power\": 370, \"rest\": 180}]. "
        "Never describe the intervals only in text and omit the array — always materialise "
        "every rep as a separate object in the array."
    )
    _rich_description_rule = (
        "Whenever you include a planUpdates entry, always include \"workoutPurpose\" "
        "(1-2 sentences on the physiological goal) and \"keyFocusPoints\" (array of 3-5 "
        "coaching-cue strings starting with an action verb). "
        "Also write \"description\" using the athlete's actual FTP/threshold HR to state "
        "exact power/HR targets — never write percentages alone."
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
            "Always include \"title\" and \"description\" so the plan entry stays informative. "
            "For a skipped/rest day set workoutType to \"rest\", durationMinutes to 0. "
            f"{_rich_description_rule} "
            f"{_intervals_rule}"
        )
    return (
        '- "planUpdates": an array of training day updates (optional). Only include this '
        "field when the athlete explicitly asks to change, swap, skip, or reschedule a "
        'workout. Each update must include "date" (ISO string matching an existing plan '
        'date) and any fields to change: "workoutType", "title", "description", '
        '"durationMinutes", "targetPower", "targetHeartRate", "intervals", '
        '"workoutPurpose", "keyFocusPoints". '
        "Always include \"title\" and \"description\" so the plan entry stays informative. "
        "For a skipped/rest day set workoutType to \"rest\", durationMinutes to 0. "
        f"{_rich_description_rule} "
        f"{_intervals_rule}"
    )


def ask_trainer_system(
    profile: dict,
    today: str,
    last_7_days: list[dict],
    next_14_days: list[dict],
    assessment_section: str,
    memory_section: str,
    workout_section: str,
    plan_updates_rule: str,
    science_context: str = "",
    training_load: dict | None = None,
    classification: dict | None = None,
) -> str:
    science_section = (
        f"\n\nRelevant cycling science research (use this to ground your advice in evidence):\n"
        f"{science_context}"
        "\nWhen citing these sources, include the title in your response."
    ) if science_context else ""

    training_load_section = ""
    if training_load:
        training_load_section = (
            f"\n\nCurrent training load: "
            f"CTL (fitness)={training_load.get('ctl')} "
            f"ATL (fatigue)={training_load.get('atl')} "
            f"TSB (form)={training_load.get('tsb')}\n"
            "Use TSB to guide your advice: TSB < −20 suggests accumulated fatigue, prioritise recovery; "
            "TSB > +10 before a key workout suggests freshness, intensity can be increased."
        )

    classification_section = ""
    if classification:
        classification_section = (
            f"\n\nQuestion classification: category={classification.get('category')} "
            f"needs_science_rag={classification.get('needs_science_rag')}"
        )

    return (
        f"{COACH_PERSONA} Answer the athlete's question concisely and practically.\n"
        f"Today's date: {today}\n"
        f"Athlete profile: {json.dumps(profile)}\n"
        f"Last 7 days of training: {json.dumps(last_7_days)}\n"
        f"Upcoming plan (next 14 days): {json.dumps(next_14_days)}"
        f"{assessment_section}"
        f"{training_load_section}"
        f"{memory_section}"
        f"{workout_section}"
        f"{classification_section}"
        f"{science_section}\n\n"
        "Before writing your response, reason through: "
        "(1) what the athlete is really asking, "
        + (
            "(2) what their current CTL/ATL/TSB suggests about their fatigue state, "
            if training_load_section else
            "(2) what their recent training history suggests about their fatigue state, "
        )
        + "(3) whether the request conflicts with training principles, "
        "(4) the most helpful coaching answer. "
        "Put this reasoning in a \"thinking\" field — it will not be shown to the athlete.\n"
        "Always take today's date into account when answering — for example when calculating "
        "days until a race, suggesting which workout is next, or referencing past sessions.\n"
        "Whenever the athlete requests a change to the training plan, your response MUST briefly "
        "reflect on whether the change is a good idea: acknowledge their preference warmly, give "
        "an honest assessment of the training impact (e.g. how it affects load, intensity, "
        "progression, or recovery), and explain any trade-offs. "
        "Always be kind, supportive, and encouraging — but never withhold honest coaching "
        "advice. If a change could harm progress or recovery, say so clearly yet tactfully, "
        "and still apply the change if the athlete wants it.\n"
        "ALWAYS respond with a valid JSON object containing exactly these fields:\n"
        '- "thinking": your internal reasoning (required, but never shown to the athlete)\n'
        '- "response": your natural language answer as a string (required)\n'
        '- "sources": an array of source titles you referenced from the science research section '
        "(omit or use [] if no research was cited)\n"
        f"{plan_updates_rule}"
    )


# ---------------------------------------------------------------------------
# update_coach_memory prompts
# ---------------------------------------------------------------------------


def update_memory_system() -> str:
    return (
        f"{COACH_PERSONA} Maintain concise notes about an athlete.\n"
        "Extract any important, actionable information from this conversation exchange and update the notes.\n"
        "Keep notes under 300 words. Focus on: goals, limitations, health issues, preferences, "
        "performance achievements, recurring problems, FTP history (record up to the 5 most recent "
        "FTP estimates with their approximate dates to track progress; drop the oldest when adding a new one), "
        "rider strengths and weaknesses, and personal motivations such as "
        "preferred terrain or event types (e.g. loves hill climbing, prefers long endurance rides).\n"
        "Return ONLY the updated notes as plain text. If nothing new and important was mentioned, return the existing notes unchanged."
    )


def update_memory_user(current_memory: str, user_message: str, coach_response: str) -> str:
    return (
        f"Existing notes:\n{current_memory or '(none)'}\n\n"
        f"Latest exchange:\nAthlete: {user_message}\nCoach: {coach_response}\n\n"
        "Update the notes with any new important information."
    )


# ---------------------------------------------------------------------------
# rate_completed_workout prompts
# ---------------------------------------------------------------------------


def rate_workout_system() -> str:
    return (
        f"{COACH_PERSONA} Review a completed training session. "
        "Compare the actual workout against the planned one and provide brief, encouraging feedback "
        "in 2-4 sentences. Note how well the athlete followed the plan, highlight any significant "
        "deviations, and explain what it means for their training progress. "
        "When objective stream data is available (power, HR, time-in-zone), use it to give "
        "precise, actionable insights — e.g. 'You went 15 % over Z2 intensity in the first 30 min, "
        "which erodes your aerobic base and costs recovery'. Otherwise use the hand-entered metrics.\n"
        "Return ONLY a valid JSON object with these fields:\n"
        '- "feedback": your 2-4 sentence coaching response as a string\n'
        '- "flag_for_adaptation": true when the athlete should adapt their upcoming plan '
        "(perceived effort ≫ planned intensity, actual duration significantly shorter than "
        "planned, or athlete notes indicate fatigue/illness/pain); otherwise false"
    )


def rate_workout_user(
    day: dict,
    feedback: dict,
    profile: dict | None = None,
    stream_delta: dict | None = None,
) -> str:
    planned_power = ""
    if day.get("targetPower"):
        planned_power = f"\n- Target power: {day['targetPower']['low']}–{day['targetPower']['high']}W"
    planned_hr = ""
    if day.get("targetHeartRate"):
        planned_hr = f"\n- Target HR: {day['targetHeartRate']['low']}–{day['targetHeartRate']['high']} bpm"
    actual_power = f"\n- Average power: {feedback['averagePower']}W" if feedback.get("averagePower") else ""
    actual_peak = f"\n- Peak power: {feedback['peakPower']}W" if feedback.get("peakPower") else ""
    actual_hr = f"\n- Average HR: {feedback['averageHeartRate']} bpm" if feedback.get("averageHeartRate") else ""
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
            lines.append(f"- Normalized power (NP): {stream_delta['normalized_power_w']}W")
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

    return (
        f"Planned workout:\n"
        f"- Type: {day['workoutType']}\n"
        f"- Title: {day['title']}\n"
        f"- Duration: {day['durationMinutes']} min\n"
        f"- Description: {day['description']}"
        f"{planned_power}{planned_hr}\n\n"
        f"Actual workout:\n"
        f"- Duration: {feedback['actualDurationMinutes']} min\n"
        f"- Perceived effort: {feedback['perceivedEffort']}/5 ({effort_labels.get(feedback['perceivedEffort'], '')})"
        f"{actual_power}{actual_peak}{actual_hr}{notes}"
        f"{delta_section}"
        f"{profile_section}\n\n"
        f"Rate how well this workout matched the plan and give brief feedback."
    )


# ---------------------------------------------------------------------------
# ask_trainer classification prompts (Task 5)
# ---------------------------------------------------------------------------


def ask_trainer_classify_system() -> str:
    return (
        "You are a routing assistant for a cycling coach chatbot. "
        "Classify the athlete's question into one of the following categories:\n"
        "- \"plan_query\": asking about their training plan, schedule, or specific workouts\n"
        "- \"workout_modification\": requesting a change, swap, or skip of a workout\n"
        "- \"performance_question\": asking about their performance, FTP, progress, or race results\n"
        "- \"science_question\": asking about training physiology, nutrition, recovery science, or methodology\n"
        "- \"general_coaching\": general coaching advice, motivation, or strategy\n"
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
        '- "loginSummary": a structured 4-part coach summary addressed directly to the athlete. '
        "Write it as flowing prose (not bullet points), 5-8 sentences total. Include:\n"
        "  (1) WHAT YOU DID: Brief overview of recent activity — volume, types, highlights, "
        "and anything that could be improved.\n"
        "  (2) FTP & FITNESS INSIGHTS: Any FTP trends or fitness observations. Is training volume "
        "too low, too high, or about right? Any signs of fatigue or fitness gains?\n"
        "  (3) PLAN ALIGNMENT: If plan context is provided, assess how well recent training "
        "matched the plan. If no plan context is available, note it briefly.\n"
        "  (4) CONCLUSIONS: 1-2 concrete, actionable recommendations for upcoming sessions.\n"
        "Be specific, warm, and encouraging — reference actual numbers from the data."
    )


def refresh_login_summary_user(
    ride_insights: str | None,
    last_ride_feedback: str | None,
    notes: str | None,
    estimated_ftp: int | None,
    training_plan: list[dict] | None,
) -> str:
    """Build the user message for login summary generation from existing assessment data."""
    parts: list[str] = []
    if estimated_ftp:
        parts.append(f"Current estimated FTP: {estimated_ftp} W")
    if notes:
        parts.append(f"Overall assessment notes:\n{notes}")
    if last_ride_feedback:
        parts.append(f"Most recent ride feedback:\n{last_ride_feedback}")
    if ride_insights:
        parts.append(f"Per-ride analysis narrative:\n{ride_insights}")
    if training_plan:
        parts.append(
            f"Current training plan (for plan alignment):\n{json.dumps(training_plan, indent=2)}"
        )
    if not parts:
        parts.append("No prior ride data available.")
    return (
        "\n\n".join(parts)
        + "\n\nGenerate a loginSummary JSON object based on the above."
    )
