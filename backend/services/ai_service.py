"""Backend AI service.

This is a focused Python port of the frontend ai.ts service. It keeps prompt
content and response shapes aligned with the existing UI so the frontend can
switch to backend fetches without semantic changes.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from google import genai
from google.genai import types
from json_repair import repair_json
from openai import AsyncOpenAI

COACH_PERSONA = (
    "You are a professional cycling coach with extensive experience in "
    "competitive road, track, and endurance cycling."
)
MAX_CONVERSATION_HISTORY = 20
OPENAI_MODEL = "gpt-4o-mini"
GEMINI_MODEL = "gemini-2.0-flash"


def _make_openai() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))


def _make_gemini() -> genai.Client:
    api_key = os.environ.get("GEMINI_API_KEY", "")
    return genai.Client(api_key=api_key)


async def _openai_chat(system_prompt: str, user_msg: str, json_mode: bool = False) -> str:
    client = _make_openai()
    kwargs: dict[str, Any] = {}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    response = await client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ],
        **kwargs,
    )
    return response.choices[0].message.content or ""


async def _openai_chat_history(
    system_prompt: str, messages: list[dict[str, str]], json_mode: bool = False
) -> str:
    client = _make_openai()
    kwargs: dict[str, Any] = {}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    response = await client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[{"role": "system", "content": system_prompt}, *messages],
        **kwargs,
    )
    return response.choices[0].message.content or ""


async def _gemini_chat(system_prompt: str, user_msg: str, json_mode: bool = False) -> str:
    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        response_mime_type="application/json" if json_mode else None,
    )
    async with _make_gemini().aio as client:
        response = await client.models.generate_content(
            model=GEMINI_MODEL,
            contents=user_msg,
            config=config,
        )
    return response.text or ""


async def _gemini_chat_history(
    system_prompt: str, messages: list[dict[str, str]], json_mode: bool = False
) -> str:
    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        response_mime_type="application/json" if json_mode else None,
    )
    contents = [
        types.Content(
            role="model" if msg["role"] == "assistant" else "user",
            parts=[types.Part.from_text(text=msg["content"])],
        )
        for msg in messages
    ]
    async with _make_gemini().aio as client:
        response = await client.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
            config=config,
        )
    return response.text or ""


def _parse_ai_json(text: str) -> Any:
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    extracted = fenced.group(1).strip() if fenced else text.strip()
    stripped = re.sub(r"(\d+)\s+[a-zA-Z_]+(?=\s*[,}\]\n])", r"\1", extracted)
    repaired = repair_json(stripped)
    return json.loads(repaired)


async def _chat(provider: str, system_prompt: str, user_msg: str, json_mode: bool = False) -> str:
    if provider == "gemini":
        return await _gemini_chat(system_prompt, user_msg, json_mode)
    return await _openai_chat(system_prompt, user_msg, json_mode)


async def _chat_history(
    provider: str, system_prompt: str, messages: list[dict[str, str]], json_mode: bool = False
) -> str:
    if provider == "gemini":
        return await _gemini_chat_history(system_prompt, messages, json_mode)
    return await _openai_chat_history(system_prompt, messages, json_mode)


async def analyse_strava_activities(activities: list[dict], provider: str = "openai") -> dict:
    system_prompt = f"{COACH_PERSONA} Analyse the provided Strava activities and return a JSON assessment.\nReturn ONLY a valid JSON object with these fields (all keys double-quoted, numeric values must be plain numbers with no units):\n- \"estimatedFTP\": integer watts, or null if insufficient power data\n- \"estimatedThresholdHR\": integer bpm, or null if insufficient heart rate data\n- \"riderType\": one of \"timetrial\", \"sprinter\", \"climber\", \"allrounder\", \"endurance\"\n- \"notes\": string summarising the athlete's strengths, weaknesses, and how this was derived\n\nGuidelines for assessment:\n- FTP estimation from power: if weighted_average_watts or average_watts is available, use the best 20-min equivalent effort ≈ 95% of best 20-min avg power. Otherwise estimate from average_watts of long sustained efforts.\n- FTP estimation from HR: if only heart rate data is available, note that FTP estimation requires power data; use HR data to assess aerobic base.\n- Threshold HR: typically the average HR during a hard 20-30 min sustained effort, or ~85-90% of max HR.\n- Rider type: analyse power distribution (high peaks vs. sustained), climb tendency (elevation gain per km), and effort duration patterns.\n- timetrial: strong sustained power, low variability, long average efforts\n- sprinter: high max power, shorter efforts, high power variability\n- climber: high elevation gain per km, longer sustained efforts at moderate power\n- endurance: long rides, moderate intensity, high volume\n- allrounder: balanced across metrics"
    user_msg = f"Last {len(activities)} Strava rides:\n{json.dumps(activities, indent=2)}\n\nAssess the rider's fitness level, estimated FTP, threshold heart rate, and rider type."
    raw = await _chat(provider, system_prompt, user_msg, json_mode=True)
    return _parse_ai_json(raw)


async def generate_training_plan(
    profile: dict, provider: str = "openai", rider_assessment: dict | None = None
) -> list[dict]:
    system_prompt = f"{COACH_PERSONA} Generate a 14-day training plan as JSON.\nReturn ONLY a valid JSON object with a \"plan\" array of training days. All keys must be double-quoted. All numeric fields must be plain numbers with no units.\nEach day must have: \"date\" (ISO date string starting from today), \"workoutType\" (one of: \"rest\",\"endurance\",\"intervals\",\"tempo\",\"race\",\"recovery\",\"strength\"), \"title\" (string), \"description\" (string), \"durationMinutes\" (integer).\nOptional fields: \"targetPower\" (object with \"low\" and \"high\" integer fields in watts), \"targetHeartRate\" (object with \"low\" and \"high\" integer fields in bpm), \"intervals\" (array of objects with \"duration\" (integer seconds), \"power\" (integer watts), \"rest\" (integer seconds)).\nPrinciples:\n- Build progressive overload over 2 weeks\n- Include rest days (1-2 per week)\n- Mix workout types based on goal\n- For FTP improvement: include threshold and VO2max work\n- For race prep: include race-specific workouts\n- Duration and intensity based on fitness level and weekly hours\n- If a rider assessment is provided, use the estimated FTP and threshold HR for precise power/HR targets\n- Tailor workout types to the rider type (e.g. more sprints for sprinters, more climbs for climbers, sustained tempo for TT riders)"
    assessment_section = f"\nRider assessment from recent Strava rides: {json.dumps(rider_assessment)}" if rider_assessment else ""
    user_msg = f"Profile: {json.dumps(profile)}{assessment_section}\nGenerate a 14-day training plan starting from today that reflects both the athlete's goals and their actual fitness level from recent rides."
    raw = await _chat(provider, system_prompt, user_msg, json_mode=True)
    parsed = _parse_ai_json(raw)
    return parsed.get("plan", [])


async def adapt_training_plan(
    plan: list[dict], recent_feedback: list[dict], profile: dict, provider: str = "openai"
) -> list[dict]:
    incomplete_days = [day for day in plan if not day.get("completed")]
    system_prompt = f"{COACH_PERSONA} Adapt the remaining training plan based on recent workout feedback.\nReturn ONLY a valid JSON object with an \"updatedDays\" array. All keys must be double-quoted. All numeric fields must be plain numbers with no units. Keep the same date fields.\nEach updated day must include all required TrainingDay fields: \"date\", \"workoutType\", \"title\", \"description\", \"durationMinutes\"."
    user_msg = f"Profile: {json.dumps(profile)}\nRecent feedback: {json.dumps(recent_feedback)}\nRemaining plan days: {json.dumps(incomplete_days)}\nAdapt the remaining days based on the feedback. Return the full updated days array."
    raw = await _chat(provider, system_prompt, user_msg, json_mode=True)
    parsed = _parse_ai_json(raw)
    updated_days = {day["date"]: day for day in parsed.get("updatedDays", [])}
    return [day if day.get("completed") else updated_days.get(day["date"], day) for day in plan]


async def ask_trainer(
    question: str,
    plan: list[dict],
    profile: dict,
    provider: str = "openai",
    coach_memory: str | None = None,
    conversation_history: list[dict[str, str]] | None = None,
) -> dict:
    today = __import__("datetime").datetime.now().date().isoformat()
    last_7_days = [day for day in plan if day.get("date", "") <= today][-7:]
    next_14_days = [day for day in plan if day.get("date", "") >= today][:14]
    memory_section = f"\n\nCoach notes about this athlete (remember these):\n{coach_memory}" if coach_memory else ""
    system_prompt = f"{COACH_PERSONA} Answer the athlete's question concisely and practically.\nAthlete profile: {json.dumps(profile)}\nLast 7 days of training: {json.dumps(last_7_days)}\nUpcoming plan (next 14 days): {json.dumps(next_14_days)}{memory_section}\n\nALWAYS respond with a valid JSON object containing exactly these fields:\n- \"response\": your natural language answer as a string (required)\n- \"planUpdates\": an array of training day updates (optional). Only include this field when the athlete explicitly asks to change, swap, skip, or reschedule a workout. Each update must include \"date\" (ISO string matching an existing plan date) and any fields to change: \"workoutType\", \"title\", \"description\", \"durationMinutes\", \"targetPower\", \"targetHeartRate\". When modifying a day, always include \"title\" and \"description\" so the plan entry stays informative. For a skipped/rest day set workoutType to \"rest\", durationMinutes to 0."
    history = (conversation_history or [])[-MAX_CONVERSATION_HISTORY:]
    messages = [*history, {"role": "user", "content": question}]
    raw = await _chat_history(provider, system_prompt, messages, json_mode=True)
    parsed = _parse_ai_json(raw)
    return {
        "response": parsed.get("response", ""),
        "plan_updates": parsed.get("planUpdates"),
    }


async def update_coach_memory(
    current_memory: str, user_message: str, coach_response: str, provider: str = "openai"
) -> str:
    system_prompt = f"{COACH_PERSONA} Maintain concise notes about an athlete.\nExtract any important, actionable information from this conversation exchange and update the notes.\nKeep notes under 300 words. Focus on: goals, limitations, health issues, preferences, performance achievements, recurring problems.\nReturn ONLY the updated notes as plain text. If nothing new and important was mentioned, return the existing notes unchanged."
    user_msg = f"Existing notes:\n{current_memory or '(none)'}\n\nLatest exchange:\nAthlete: {user_message}\nCoach: {coach_response}\n\nUpdate the notes with any new important information."
    return await _chat(provider, system_prompt, user_msg)


async def rate_completed_workout(day: dict, profile: dict, provider: str = "openai") -> str:
    feedback = day.get("feedback")
    if not feedback:
        return ""

    system_prompt = f"{COACH_PERSONA} Review a completed training session. Compare the actual workout against the planned one and provide brief, encouraging feedback in 2-4 sentences. Note how well the athlete followed the plan, highlight any significant deviations, and explain what it means for their training progress."

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

    user_msg = f"Planned workout:\n- Type: {day['workoutType']}\n- Title: {day['title']}\n- Duration: {day['durationMinutes']} min\n- Description: {day['description']}{planned_power}{planned_hr}\n\nActual workout:\n- Duration: {feedback['actualDurationMinutes']} min\n- Perceived effort: {feedback['perceivedEffort']}/5 ({effort_labels.get(feedback['perceivedEffort'], '')}){actual_power}{actual_peak}{actual_hr}{notes}\n\nAthlete profile: {json.dumps(profile)}\n\nRate how well this workout matched the plan and give brief feedback."
    return await _chat(provider, system_prompt, user_msg)
