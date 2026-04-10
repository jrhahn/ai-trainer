import OpenAI from 'openai'
import { GoogleGenerativeAI } from '@google/generative-ai'
import type { UserProfile, TrainingDay, WorkoutFeedback } from '../store/useAppStore'

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

/** Extract JSON text from a Gemini response that may wrap it in a markdown code fence */
function extractJson(text: string): string {
  const fenced = text.match(/```(?:json)?\s*([\s\S]*?)```/)
  return fenced ? fenced[1].trim() : text.trim()
}

// ─── public API ──────────────────────────────────────────────────────────────

export async function generateTrainingPlan(
  profile: UserProfile,
  apiKey: string,
  provider: AiProvider = 'openai'
): Promise<TrainingDay[]> {
  const systemPrompt = `You are an expert cycling coach. Generate a 28-day training plan as JSON.
Return ONLY a JSON object with a "plan" array of training days.
Each day must have: date (ISO string starting from today), workoutType (rest|endurance|intervals|tempo|race|recovery|strength), title, description, durationMinutes.
Optional fields: targetPower ({low, high}), targetHeartRate ({low, high}), intervals (array of {duration (seconds), power (watts), rest (seconds)}).
Principles:
- Build progressive overload over 4 weeks
- Include rest days (1-2 per week)
- Mix workout types based on goal
- For FTP improvement: include threshold and VO2max work
- For race prep: include race-specific workouts
- Duration and intensity based on fitness level and weekly hours`

  const userMsg = `Profile: ${JSON.stringify(profile)}
Generate a 28-day training plan starting from today (${new Date().toISOString().split('T')[0]}).`

  let raw: string
  if (provider === 'gemini') {
    raw = await geminiChat(apiKey, 'gemini-2.0-flash', systemPrompt, userMsg, true)
  } else {
    raw = await openaiChat(apiKey, 'gpt-4o-mini', systemPrompt, userMsg, true)
  }

  const parsed = JSON.parse(extractJson(raw)) as { plan: TrainingDay[] }
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
  const systemPrompt = `You are an expert cycling coach. Adapt the remaining training plan based on recent workout feedback.
Return ONLY a JSON object with an "updatedDays" array. Keep the same date fields.
Each updated day must include all required TrainingDay fields.`

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

  const parsed = JSON.parse(extractJson(raw)) as { updatedDays: TrainingDay[] }
  const updatedMap = new Map(parsed.updatedDays.map((d) => [d.date, d]))
  return plan.map((d) => (d.completed ? d : (updatedMap.get(d.date) ?? d)))
}

export async function askTrainer(
  question: string,
  plan: TrainingDay[],
  profile: UserProfile,
  apiKey: string,
  provider: AiProvider = 'openai'
): Promise<string> {
  const systemPrompt = `You are a friendly, expert cycling coach. Answer the athlete's question concisely.
You have access to their training plan and profile. Be practical and specific.`

  const userMsg = `Profile: ${JSON.stringify(profile)}
Upcoming plan (next 7 days): ${JSON.stringify(plan.slice(0, 7))}
Question: ${question}`

  if (provider === 'gemini') {
    return geminiChat(apiKey, 'gemini-2.0-flash', systemPrompt, userMsg)
  }
  return openaiChat(apiKey, 'gpt-4o-mini', systemPrompt, userMsg)
}
