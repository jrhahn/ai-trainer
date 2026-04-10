import OpenAI from 'openai'
import { GoogleGenerativeAI } from '@google/generative-ai'
import { jsonrepair } from 'jsonrepair'
import type { UserProfile, TrainingDay, WorkoutFeedback, StravaActivity, RiderAssessment } from '../store/useAppStore'

export type AiProvider = 'openai' | 'gemini'

// ─── helpers ─────────────────────────────────────────────────────────────────

function makeOpenAI(apiKey: string) {
  return new OpenAI({ apiKey, dangerouslyAllowBrowser: true })
}

function makeGemini(apiKey: string) {
  return new GoogleGenerativeAI(apiKey)
}

/** Call OpenAI chat completion and return raw text */
async function openaiChat(
  apiKey: string,
  model: string,
  systemPrompt: string,
  userMsg: string,
  jsonMode = false
): Promise<string> {
  const client = makeOpenAI(apiKey)
  const resp = await client.chat.completions.create({
    model,
    ...(jsonMode ? { response_format: { type: 'json_object' } } : {}),
    messages: [
      { role: 'system', content: systemPrompt },
      { role: 'user', content: userMsg },
    ],
  })
  return resp.choices[0].message.content ?? ''
}

/** Call Gemini and return raw text */
async function geminiChat(
  apiKey: string,
  model: string,
  systemPrompt: string,
  userMsg: string,
  jsonMode = false
): Promise<string> {
  const genAI = makeGemini(apiKey)
  const genModel = genAI.getGenerativeModel({
    model,
    systemInstruction: systemPrompt,
    ...(jsonMode
      ? { generationConfig: { responseMimeType: 'application/json' } }
      : {}),
  })
  const result = await genModel.generateContent(userMsg)
  return result.response.text()
}

type ConversationMessage = { role: 'user' | 'assistant'; content: string }

/** Call OpenAI with a full conversation history */
async function openaiChatHistory(
  apiKey: string,
  model: string,
  systemPrompt: string,
  messages: ConversationMessage[],
  jsonMode = false
): Promise<string> {
  const client = makeOpenAI(apiKey)
  const resp = await client.chat.completions.create({
    model,
    ...(jsonMode ? { response_format: { type: 'json_object' } } : {}),
    messages: [
      { role: 'system', content: systemPrompt },
      ...messages.map((m) => ({ role: m.role, content: m.content })),
    ],
  })
  return resp.choices[0].message.content ?? ''
}

/** Call Gemini with a full conversation history using startChat */
async function geminiChatHistory(
  apiKey: string,
  model: string,
  systemPrompt: string,
  messages: ConversationMessage[],
  jsonMode = false
): Promise<string> {
  const genAI = makeGemini(apiKey)
  const genModel = genAI.getGenerativeModel({
    model,
    systemInstruction: systemPrompt,
    ...(jsonMode ? { generationConfig: { responseMimeType: 'application/json' } } : {}),
  })

  const history = messages.slice(0, -1).map((m) => ({
    role: m.role === 'assistant' ? 'model' : ('user' as const),
    parts: [{ text: m.content }],
  }))

  const chat = genModel.startChat({ history })
  const lastMsg = messages[messages.length - 1].content
  const result = await chat.sendMessage(lastMsg)
  return result.response.text()
}

/** Extract JSON from a response that may wrap it in a markdown code fence, then
 *  strip trailing unit words from numeric values (e.g. `90 minutes` → `90`)
 *  and repair any other minor syntax issues before parsing, so that
 *  malformed-but-fixable AI output doesn't throw. */
function parseAiJson<T>(text: string): T {
  const fenced = text.match(/```(?:json)?\s*([\s\S]*?)```/)
  const extracted = fenced ? fenced[1].trim() : text.trim()
  // Strip unit words that appear after numbers and before JSON delimiters
  // e.g. `"durationMinutes": 90 minutes,` → `"durationMinutes": 90,`
  const stripped = extracted.replace(/(\d+)\s+[a-zA-Z_]+(?=\s*[,\}\]\n])/g, '$1')
  return JSON.parse(jsonrepair(stripped)) as T
}

// ─── constants ───────────────────────────────────────────────────────────────

const MAX_CONVERSATION_HISTORY = 20

/** Shared identity statement prepended to every system prompt so the model
 *  always knows it is acting as a professional cycling coach. */
const COACH_PERSONA =
  'You are a professional cycling coach with extensive experience in competitive road, track, and endurance cycling.'

// ─── public API ──────────────────────────────────────────────────────────────

export { MAX_CONVERSATION_HISTORY }

export async function analyseStravaActivities(
  activities: StravaActivity[],
  apiKey: string,
  provider: AiProvider = 'openai'
): Promise<RiderAssessment> {
  const systemPrompt = `${COACH_PERSONA} Analyse the provided Strava activities and return a JSON assessment.
Return ONLY a valid JSON object with these fields (all keys double-quoted, numeric values must be plain numbers with no units):
- "estimatedFTP": integer watts, or null if insufficient power data
- "estimatedThresholdHR": integer bpm, or null if insufficient heart rate data
- "riderType": one of "timetrial", "sprinter", "climber", "allrounder", "endurance"
- "notes": string summarising the athlete's strengths, weaknesses, and how this was derived

Guidelines for assessment:
- FTP estimation from power: if weighted_average_watts or average_watts is available, use the best 20-min equivalent effort ≈ 95% of best 20-min avg power. Otherwise estimate from average_watts of long sustained efforts.
- FTP estimation from HR: if only heart rate data is available, note that FTP estimation requires power data; use HR data to assess aerobic base.
- Threshold HR: typically the average HR during a hard 20-30 min sustained effort, or ~85-90% of max HR.
- Rider type: analyse power distribution (high peaks vs. sustained), climb tendency (elevation gain per km), and effort duration patterns.
- timetrial: strong sustained power, low variability, long average efforts
- sprinter: high max power, shorter efforts, high power variability
- climber: high elevation gain per km, longer sustained efforts at moderate power
- endurance: long rides, moderate intensity, high volume
- allrounder: balanced across metrics`

  const userMsg = `Last ${activities.length} Strava rides:
${JSON.stringify(activities, null, 2)}

Assess the rider's fitness level, estimated FTP, threshold heart rate, and rider type.`

  let raw: string
  if (provider === 'gemini') {
    raw = await geminiChat(apiKey, 'gemini-2.0-flash', systemPrompt, userMsg, true)
  } else {
    raw = await openaiChat(apiKey, 'gpt-4o-mini', systemPrompt, userMsg, true)
  }

  const parsed = parseAiJson<RiderAssessment>(raw)
  return parsed
}

export async function generateTrainingPlan(
  profile: UserProfile,
  apiKey: string,
  provider: AiProvider = 'openai',
  riderAssessment?: RiderAssessment
): Promise<TrainingDay[]> {
  const systemPrompt = `${COACH_PERSONA} Generate a 28-day training plan as JSON.
Return ONLY a valid JSON object with a "plan" array of training days. All keys must be double-quoted. All numeric fields must be plain numbers with no units.
Each day must have: "date" (ISO date string starting from today), "workoutType" (one of: "rest","endurance","intervals","tempo","race","recovery","strength"), "title" (string), "description" (string), "durationMinutes" (integer).
Optional fields: "targetPower" (object with "low" and "high" integer fields in watts), "targetHeartRate" (object with "low" and "high" integer fields in bpm), "intervals" (array of objects with "duration" (integer seconds), "power" (integer watts), "rest" (integer seconds)).
Principles:
- Build progressive overload over 4 weeks
- Include rest days (1-2 per week)
- Mix workout types based on goal
- For FTP improvement: include threshold and VO2max work
- For race prep: include race-specific workouts
- Duration and intensity based on fitness level and weekly hours
- If a rider assessment is provided, use the estimated FTP and threshold HR for precise power/HR targets
- Tailor workout types to the rider type (e.g. more sprints for sprinters, more climbs for climbers, sustained tempo for TT riders)`

  const assessmentSection = riderAssessment
    ? `\nRider assessment from recent Strava rides: ${JSON.stringify(riderAssessment)}`
    : ''

  const userMsg = `Profile: ${JSON.stringify(profile)}${assessmentSection}
Generate a 28-day training plan starting from today (${new Date().toISOString().split('T')[0]}) that reflects both the athlete's goals and their actual fitness level from recent rides.`

  let raw: string
  if (provider === 'gemini') {
    raw = await geminiChat(apiKey, 'gemini-2.0-flash', systemPrompt, userMsg, true)
  } else {
    raw = await openaiChat(apiKey, 'gpt-4o-mini', systemPrompt, userMsg, true)
  }

  const parsed = parseAiJson<{ plan: TrainingDay[] }>(raw)
  return parsed.plan ?? []
}

export async function adaptTrainingPlan(
  plan: TrainingDay[],
  recentFeedback: WorkoutFeedback[],
  profile: UserProfile,
  apiKey: string,
  provider: AiProvider = 'openai'
): Promise<TrainingDay[]> {
  const incompleteDays = plan.filter((d) => !d.completed)
  const systemPrompt = `${COACH_PERSONA} Adapt the remaining training plan based on recent workout feedback.
Return ONLY a valid JSON object with an "updatedDays" array. All keys must be double-quoted. All numeric fields must be plain numbers with no units. Keep the same date fields.
Each updated day must include all required TrainingDay fields: "date", "workoutType", "title", "description", "durationMinutes".`

  const userMsg = `Profile: ${JSON.stringify(profile)}
Recent feedback: ${JSON.stringify(recentFeedback)}
Remaining plan days: ${JSON.stringify(incompleteDays)}
Adapt the remaining days based on the feedback. Return the full updated days array.`

  let raw: string
  if (provider === 'gemini') {
    raw = await geminiChat(apiKey, 'gemini-2.0-flash', systemPrompt, userMsg, true)
  } else {
    raw = await openaiChat(apiKey, 'gpt-4o-mini', systemPrompt, userMsg, true)
  }

  const parsed = parseAiJson<{ updatedDays: TrainingDay[] }>(raw)
  const updatedMap = new Map(parsed.updatedDays.map((d) => [d.date, d]))
  return plan.map((d) => (d.completed ? d : (updatedMap.get(d.date) ?? d)))
}

export interface AskTrainerOptions {
  coachMemory?: string
  conversationHistory?: ConversationMessage[]
}

/** A single training day modification proposed by the AI coach. */
export interface PlanDayUpdate {
  date: string
  workoutType?: TrainingDay['workoutType']
  title?: string
  description?: string
  durationMinutes?: number
  targetPower?: TrainingDay['targetPower']
  targetHeartRate?: TrainingDay['targetHeartRate']
}

/** Structured response from askTrainer. */
export interface AskTrainerResult {
  response: string
  planUpdates?: PlanDayUpdate[]
}

export async function askTrainer(
  question: string,
  plan: TrainingDay[],
  profile: UserProfile,
  apiKey: string,
  provider: AiProvider = 'openai',
  options: AskTrainerOptions = {}
): Promise<AskTrainerResult> {
  const today = new Date().toISOString().split('T')[0]
  const sevenDaysAgo = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000).toISOString().split('T')[0]
  const last7Days = plan.filter((d) => d.date >= sevenDaysAgo && d.date <= today)
  const next14Days = plan.filter((d) => d.date >= today).slice(0, 14)

  const memorySection = options.coachMemory
    ? `\n\nCoach notes about this athlete (remember these):\n${options.coachMemory}`
    : ''

  const systemPrompt = `${COACH_PERSONA} Answer the athlete's question concisely and practically.
Athlete profile: ${JSON.stringify(profile)}
Last 7 days of training: ${JSON.stringify(last7Days)}
Upcoming plan (next 14 days): ${JSON.stringify(next14Days)}${memorySection}

ALWAYS respond with a valid JSON object containing exactly these fields:
- "response": your natural language answer as a string (required)
- "planUpdates": an array of training day updates (optional). Only include this field when the athlete explicitly asks to change, swap, skip, or reschedule a workout. Each update must include "date" (ISO string matching an existing plan date) and any fields to change: "workoutType", "title", "description", "durationMinutes", "targetPower", "targetHeartRate". When modifying a day, always include "title" and "description" so the plan entry stays informative. For a skipped/rest day set workoutType to "rest", durationMinutes to 0.`

  const history = options.conversationHistory ?? []
  const messages: ConversationMessage[] = [...history, { role: 'user', content: question }]

  let raw: string
  if (provider === 'gemini') {
    raw = await geminiChatHistory(apiKey, 'gemini-2.0-flash', systemPrompt, messages, true)
  } else {
    raw = await openaiChatHistory(apiKey, 'gpt-4o-mini', systemPrompt, messages, true)
  }

  const parsed = parseAiJson<{ response: string; planUpdates?: PlanDayUpdate[] }>(raw)
  return { response: parsed.response ?? '', planUpdates: parsed.planUpdates }
}

export async function updateCoachMemory(
  currentMemory: string,
  userMessage: string,
  coachResponse: string,
  apiKey: string,
  provider: AiProvider = 'openai'
): Promise<string> {
  const systemPrompt = `${COACH_PERSONA} Maintain concise notes about an athlete.
Extract any important, actionable information from this conversation exchange and update the notes.
Keep notes under 300 words. Focus on: goals, limitations, health issues, preferences, performance achievements, recurring problems.
Return ONLY the updated notes as plain text. If nothing new and important was mentioned, return the existing notes unchanged.`

  const userMsg = `Existing notes:
${currentMemory || '(none)'}

Latest exchange:
Athlete: ${userMessage}
Coach: ${coachResponse}

Update the notes with any new important information.`

  if (provider === 'gemini') {
    return geminiChat(apiKey, 'gemini-2.0-flash', systemPrompt, userMsg)
  }
  return openaiChat(apiKey, 'gpt-4o-mini', systemPrompt, userMsg)
}

export async function rateCompletedWorkout(
  day: TrainingDay,
  profile: UserProfile,
  apiKey: string,
  provider: AiProvider = 'openai'
): Promise<string> {
  if (!day.feedback) return ''

  const systemPrompt = `${COACH_PERSONA} Review a completed training session. Compare the actual workout against the planned one and provide brief, encouraging feedback in 2-4 sentences. Note how well the athlete followed the plan, highlight any significant deviations, and explain what it means for their training progress.`

  const plannedPower = day.targetPower
    ? `\n- Target power: ${day.targetPower.low}–${day.targetPower.high}W`
    : ''
  const plannedHR = day.targetHeartRate
    ? `\n- Target HR: ${day.targetHeartRate.low}–${day.targetHeartRate.high} bpm`
    : ''
  const actualPower = day.feedback.averagePower
    ? `\n- Average power: ${day.feedback.averagePower}W`
    : ''
  const actualPeak = day.feedback.peakPower ? `\n- Peak power: ${day.feedback.peakPower}W` : ''
  const actualHR = day.feedback.averageHeartRate
    ? `\n- Average HR: ${day.feedback.averageHeartRate} bpm`
    : ''
  const notes = day.feedback.notes ? `\n- Notes: ${day.feedback.notes}` : ''

  const effortLabels: Record<number, string> = {
    1: 'Easy',
    2: 'Moderate',
    3: 'Hard',
    4: 'Very Hard',
    5: 'Max',
  }

  const userMsg = `Planned workout:
- Type: ${day.workoutType}
- Title: ${day.title}
- Duration: ${day.durationMinutes} min
- Description: ${day.description}${plannedPower}${plannedHR}

Actual workout:
- Duration: ${day.feedback.actualDurationMinutes} min
- Perceived effort: ${day.feedback.perceivedEffort}/5 (${effortLabels[day.feedback.perceivedEffort] ?? ''})${actualPower}${actualPeak}${actualHR}${notes}

Athlete profile: ${JSON.stringify(profile)}

Rate how well this workout matched the plan and give brief feedback.`

  if (provider === 'gemini') {
    return geminiChat(apiKey, 'gemini-2.0-flash', systemPrompt, userMsg)
  }
  return openaiChat(apiKey, 'gpt-4o-mini', systemPrompt, userMsg)
}
