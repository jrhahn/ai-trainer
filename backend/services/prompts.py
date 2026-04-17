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


def analyse_activities_system() -> str:
    return (
        f"{COACH_PERSONA} Analyse the provided Strava activities and return a JSON assessment.\n"
        "Return ONLY a valid JSON object with these fields "
        "(all keys double-quoted, numeric values must be plain numbers with no units):\n"
        "- \"estimatedFTP\": integer watts — use the pre-computed value when provided, "
        "otherwise estimate from activity summaries; null if no power data\n"
        "- \"estimatedThresholdHR\": integer bpm — use the pre-computed value when provided, "
        "otherwise estimate from activity summaries; null if no HR data\n"
        "- \"riderType\": one of \"timetrial\", \"sprinter\", \"climber\", \"allrounder\", \"endurance\"\n"
        "- \"notes\": a concise overall assessment addressed directly to the athlete using 'you'. "
        "Mention their strengths, rider type, and key observations from their rides. "
        "Example: 'You show strong sustained power over long efforts, which marks you as a time-trial type rider. "
        "Your aerobic base looks solid…'\n"
        "- \"lastRideFeedback\": a standalone 3-5 sentence coach note about the SINGLE MOST RECENT ride only "
        "(the one with the latest start_date). Write it as a card the athlete reads first thing on their dashboard. "
        "Cover: (1) what type of ride it was (category) and key numbers, "
        "(2) how the effort looked — power consistency and HR response or drift if data available, "
        "(3) one concrete recommendation for the next training session. "
        "Be warm, personal, and specific — use their actual numbers.\n"
        "- \"rideInsights\": a per-ride narrative addressed to the athlete. "
        "For each ride: state its category (recovery/endurance/tempo/sweet-spot/threshold/VO2max/sprints), "
        "comment on the interval quality (power consistency, HR drift if data available), and give one "
        "concrete takeaway. Also include 1-2 specific recommendations for the athlete's next training "
        "session based on what you observed. Be empathetic and personal — reference their specific numbers.\n"
        "- \"planUpdates\": optional array of training day updates for the upcoming plan based on what "
        "you observed in the rides. Only include updates that are genuinely warranted (e.g. add recovery "
        "if athlete shows fatigue/HR drift, increase intensity if athlete is clearly above their current "
        "targets). Each update: {\"date\": \"<ISO date>\", \"workoutType\": \"<type>\", \"title\": "
        "\"<string>\", \"description\": \"<string>\", \"durationMinutes\": <int>}. "
        "If no updates are needed, omit this field or set it to [].\n\n"
        "Ride categories:\n"
        "- recovery: avg power < 60% FTP\n"
        "- endurance: avg power 60–75% FTP, no distinct intervals\n"
        "- tempo: avg power ~76–85% FTP, no distinct intervals\n"
        "- interval_sweetspot: 10–20 min efforts at 88–95% FTP\n"
        "- interval_threshold: ~5–12 min efforts at 95–105% FTP\n"
        "- interval_vo2max: 2–5 min efforts at 106–130% FTP\n"
        "- interval_sprints: < 2 min efforts at > 130% FTP\n\n"
        "Rider-type guidelines:\n"
        "- timetrial: strong sustained power, low variability, long average efforts\n"
        "- sprinter: high max power, shorter efforts, high power variability\n"
        "- climber: high elevation gain per km, longer sustained efforts at moderate power\n"
        "- endurance: long rides, moderate intensity, high volume\n"
        "- allrounder: balanced across metrics"
    )


def analyse_activities_user(
    activities: list[dict],
    computed_section: str,
    ride_analyses_section: str,
) -> str:
    return (
        f"Last {len(activities)} Strava rides:\n{json.dumps(activities, indent=2)}"
        f"{computed_section}"
        f"{ride_analyses_section}\n\n"
        "Assess my fitness. When pre-computed FTP/threshold HR values are given, "
        "use them verbatim for estimatedFTP and estimatedThresholdHR. "
        "Use the per-ride analyses above to write accurate rideInsights and appropriate planUpdates."
    )


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
        "\"recovery\",\"strength\"), \"title\" (string), \"description\" (string), "
        "\"durationMinutes\" (integer).\n"
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
        "Keep the same date fields.\n"
        "Each updated day must include all required TrainingDay fields: "
        "\"date\", \"workoutType\", \"title\", \"description\", \"durationMinutes\"."
    )


def adapt_plan_user(
    profile: dict,
    today: str,
    recent_feedback: list[dict],
    incomplete_days: list[dict],
) -> str:
    return (
        f"Today's date: {today}\n"
        f"Profile: {json.dumps(profile)}\n"
        f"Recent feedback: {json.dumps(recent_feedback)}\n"
        f"Remaining plan days: {json.dumps(incomplete_days)}\n"
        "Adapt the remaining days based on the feedback. Return the full updated days array."
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
    if context_workout:
        return (
            '- "planUpdates": an array of training day updates. '
            "Include this field in two cases: "
            "(1) The athlete explicitly asks to change, swap, skip, or reschedule any workout. "
            "(2) You propose a coaching change to the currently-viewed workout (see above) — "
            "even if the athlete did not explicitly request a change. "
            'Each update must include "date" (ISO string matching an existing plan date) and any '
            'fields to change: "workoutType", "title", "description", "durationMinutes", '
            '"targetPower", "targetHeartRate", "intervals". '
            "Always include \"title\" and \"description\" so the plan entry stays informative. "
            "For a skipped/rest day set workoutType to \"rest\", durationMinutes to 0. "
            f"{_intervals_rule}"
        )
    return (
        '- "planUpdates": an array of training day updates (optional). Only include this '
        "field when the athlete explicitly asks to change, swap, skip, or reschedule a "
        'workout. Each update must include "date" (ISO string matching an existing plan '
        'date) and any fields to change: "workoutType", "title", "description", '
        '"durationMinutes", "targetPower", "targetHeartRate", "intervals". '
        "Always include \"title\" and \"description\" so the plan entry stays informative. "
        "For a skipped/rest day set workoutType to \"rest\", durationMinutes to 0. "
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
) -> str:
    return (
        f"{COACH_PERSONA} Answer the athlete's question concisely and practically.\n"
        f"Today's date: {today}\n"
        f"Athlete profile: {json.dumps(profile)}\n"
        f"Last 7 days of training: {json.dumps(last_7_days)}\n"
        f"Upcoming plan (next 14 days): {json.dumps(next_14_days)}"
        f"{assessment_section}"
        f"{memory_section}"
        f"{workout_section}\n\n"
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
        '- "response": your natural language answer as a string (required)\n'
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
        "deviations, and explain what it means for their training progress."
    )


def rate_workout_user(day: dict, feedback: dict, profile: dict | None = None) -> str:
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
        f"{profile_section}\n\n"
        f"Rate how well this workout matched the plan and give brief feedback."
    )
