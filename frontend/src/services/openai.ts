import OpenAI from 'openai'
import type { UserProfile, TrainingDay, WorkoutFeedback } from '../store/useAppStore'

function makeClient(apiKey: string) {
  return new OpenAI({ apiKey, dangerouslyAllowBrowser: true })
}

export async function generateTrainingPlan(
  profile: UserProfile,
  apiKey: string
): Promise<TrainingDay[]> {
  const client = makeClient(apiKey)

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

  const resp = await client.chat.completions.create({
    model: 'gpt-4o-mini',
    response_format: { type: 'json_object' },
    messages: [
      { role: 'system', content: systemPrompt },
      { role: 'user', content: userMsg },
    ],
  })

  const content = resp.choices[0].message.content ?? '{}'
  const parsed = JSON.parse(content) as { plan: TrainingDay[] }
  return parsed.plan ?? []
}

export async function adaptTrainingPlan(
  plan: TrainingDay[],
  recentFeedback: WorkoutFeedback[],
  profile: UserProfile,
  apiKey: string
): Promise<TrainingDay[]> {
  const client = makeClient(apiKey)

  const incompleteDays = plan.filter((d) => !d.completed)
  const systemPrompt = `You are an expert cycling coach. Adapt the remaining training plan based on recent workout feedback.
Return ONLY a JSON object with an "updatedDays" array. Keep the same date fields.
Each updated day must include all required TrainingDay fields.`

  const resp = await client.chat.completions.create({
    model: 'gpt-4o-mini',
    response_format: { type: 'json_object' },
    messages: [
      { role: 'system', content: systemPrompt },
      {
        role: 'user',
        content: `Profile: ${JSON.stringify(profile)}
Recent feedback: ${JSON.stringify(recentFeedback)}
Remaining plan days: ${JSON.stringify(incompleteDays)}
Adapt the remaining days based on the feedback. Return the full updated days array.`,
      },
    ],
  })

  const content = resp.choices[0].message.content ?? '{}'
  const parsed = JSON.parse(content) as { updatedDays: TrainingDay[] }
  const updatedMap = new Map(parsed.updatedDays.map((d) => [d.date, d]))

  return plan.map((d) => (d.completed ? d : (updatedMap.get(d.date) ?? d)))
}

export async function askTrainer(
  question: string,
  plan: TrainingDay[],
  profile: UserProfile,
  apiKey: string
): Promise<string> {
  const client = makeClient(apiKey)

  const systemPrompt = `You are a friendly, expert cycling coach. Answer the athlete's question concisely.
You have access to their training plan and profile. Be practical and specific.`

  const resp = await client.chat.completions.create({
    model: 'gpt-4o-mini',
    messages: [
      { role: 'system', content: systemPrompt },
      {
        role: 'user',
        content: `Profile: ${JSON.stringify(profile)}
Upcoming plan (next 7 days): ${JSON.stringify(plan.slice(0, 7))}
Question: ${question}`,
      },
    ],
  })

  return resp.choices[0].message.content ?? 'Sorry, I could not generate a response.'
}
