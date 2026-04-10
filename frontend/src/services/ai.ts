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

type ConversationMessage = { role: 'user' | 'assistant'; content: string }

/** Call OpenAI with a full conversation history */
async function openaiChatHistory(
  apiKey: string,
  model: string,
  systemPrompt: string,
  messages: ConversationMessage[]
): Promise<string> {
  const client = makeOpenAI(apiKey)
  const resp = await client.chat.completions.create({
    model,
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
  messages: ConversationMessage[]
): Promise<string> {
  const genAI = makeGemini(apiKey)
  const genModel = genAI.getGenerativeModel({ model, systemInstruction: systemPrompt })

  const history = messages.slice(0, -1).map((m) => ({
    role: m.role === 'assistant' ? 'model' : ('user' as const),
    parts: [{ text: m.content }],
  }))

  const chat = genModel.startChat({ history })
  const lastMsg = messages[messages.length - 1].content
  const result = await chat.sendMessage(lastMsg)
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

export interface AskTrainerOptions {
  coachMemory?: string
  conversationHistory?: ConversationMessage[]
}

export async function askTrainer(
  question: string,
  plan: TrainingDay[],
  profile: UserProfile,
  apiKey: string,
  provider: AiProvider = 'openai',
  options: AskTrainerOptions = {}
): Promise<string> {
  const today = new Date().toISOString().split('T')[0]
  const sevenDaysAgo = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000).toISOString().split('T')[0]
  const last7Days = plan.filter((d) => d.date >= sevenDaysAgo && d.date < today)
  const next7Days = plan.filter((d) => d.date >= today).slice(0, 7)

  const memorySection = options.coachMemory
    ? `\n\nCoach notes about this athlete (remember these):\n${options.coachMemory}`
    : ''

  const systemPrompt = `You are a friendly, expert cycling coach. Answer the athlete's question concisely and practically.
Athlete profile: ${JSON.stringify(profile)}
Last 7 days of training: ${JSON.stringify(last7Days)}
Upcoming plan (next 7 days): ${JSON.stringify(next7Days)}${memorySection}`

  const history = options.conversationHistory ?? []
  const messages: ConversationMessage[] = [...history, { role: 'user', content: question }]

  if (provider === 'gemini') {
    return geminiChatHistory(apiKey, 'gemini-2.0-flash', systemPrompt, messages)
  }
  return openaiChatHistory(apiKey, 'gpt-4o-mini', systemPrompt, messages)
}

export async function updateCoachMemory(
  currentMemory: string,
  userMessage: string,
  coachResponse: string,
  apiKey: string,
  provider: AiProvider = 'openai'
): Promise<string> {
  const systemPrompt = `You are a cycling coach maintaining concise notes about an athlete.
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
