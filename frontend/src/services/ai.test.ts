import { describe, it, expect, vi, beforeEach } from 'vitest'
import type { UserProfile, TrainingDay } from '../store/useAppStore'

// ─── Hoisted mocks (needed because vi.mock is hoisted above imports) ───────────

const mockCreate = vi.hoisted(() => vi.fn())
const mockSendMessage = vi.hoisted(() => vi.fn())
const mockGenerateContent = vi.hoisted(() => vi.fn())

// ─── Mock OpenAI ─────────────────────────────────────────────────────────────

vi.mock('openai', () => ({
  default: vi.fn(function () {
    return { chat: { completions: { create: mockCreate } } }
  }),
}))

// ─── Mock Gemini ──────────────────────────────────────────────────────────────

vi.mock('@google/generative-ai', () => ({
  GoogleGenerativeAI: vi.fn(function () {
    return {
      getGenerativeModel: vi.fn(function () {
        return {
          generateContent: mockGenerateContent,
          startChat: vi.fn(function () {
            return { sendMessage: mockSendMessage }
          }),
        }
      }),
    }
  }),
}))

// ─── Import after mocks ───────────────────────────────────────────────────────

import {
  MAX_CONVERSATION_HISTORY,
  askTrainer,
  updateCoachMemory,
  generateTrainingPlan,
  adaptTrainingPlan,
  rateCompletedWorkout,
} from '../services/ai'

// ─── Helpers ─────────────────────────────────────────────────────────────────

const profile: UserProfile = {
  name: 'Alice',
  email: 'alice@example.com',
  bikeType: 'road',
  trainingGoal: 'ftp_improvement',
  weeklyHours: 10,
  followsTrainingPlan: true,
  fitnessLevel: 'intermediate',
}

function makeDay(date: string, completed = false): TrainingDay {
  return {
    date,
    workoutType: 'endurance',
    title: 'Easy Ride',
    description: 'Z2 ride',
    durationMinutes: 90,
    completed,
  }
}

function mockOpenAIResponse(content: string) {
  mockCreate.mockResolvedValue({ choices: [{ message: { content } }] })
}

function mockGeminiResponse(content: string) {
  mockGenerateContent.mockResolvedValue({ response: { text: () => content } })
  mockSendMessage.mockResolvedValue({ response: { text: () => content } })
}

beforeEach(() => {
  vi.clearAllMocks()
})

// ─── Tests ───────────────────────────────────────────────────────────────────

describe('MAX_CONVERSATION_HISTORY', () => {
  it('is 20', () => {
    expect(MAX_CONVERSATION_HISTORY).toBe(20)
  })
})

describe('askTrainer', () => {
  it('returns the OpenAI response text', async () => {
    mockOpenAIResponse(JSON.stringify({ response: 'Try interval training twice a week.' }))
    const result = await askTrainer('How should I train?', [], profile, 'sk-test')
    expect(result.response).toBe('Try interval training twice a week.')
  })

  it('includes coach memory in the system prompt when provided', async () => {
    mockOpenAIResponse(JSON.stringify({ response: 'Sure!' }))
    await askTrainer('Any tips?', [], profile, 'sk-test', 'openai', {
      coachMemory: 'Athlete has a knee injury.',
    })
    const systemPrompt: string = (mockCreate.mock.calls[0][0] as { messages: Array<{ role: string; content: string }> }).messages[0].content
    expect(systemPrompt).toContain('Athlete has a knee injury.')
  })

  it('does not include a memory section when coachMemory is absent', async () => {
    mockOpenAIResponse(JSON.stringify({ response: 'Sure!' }))
    await askTrainer('Any tips?', [], profile, 'sk-test')
    const systemPrompt: string = (mockCreate.mock.calls[0][0] as { messages: Array<{ role: string; content: string }> }).messages[0].content
    expect(systemPrompt).not.toContain('Coach notes')
  })

  it('includes the last 7 days of completed training in the system prompt', async () => {
    mockOpenAIResponse(JSON.stringify({ response: 'Great job!' }))
    const today = new Date().toISOString().split('T')[0]
    const yesterday = new Date(Date.now() - 86400000).toISOString().split('T')[0]
    const twoWeeksAgo = new Date(Date.now() - 14 * 86400000).toISOString().split('T')[0]
    const plan = [makeDay(twoWeeksAgo, true), makeDay(yesterday, true), makeDay(today, false)]

    await askTrainer('How am I doing?', plan, profile, 'sk-test')
    const systemPrompt: string = (mockCreate.mock.calls[0][0] as { messages: Array<{ role: string; content: string }> }).messages[0].content
    expect(systemPrompt).toContain(yesterday)
    expect(systemPrompt).not.toContain(twoWeeksAgo)
  })

  it('passes the conversation history as messages (excluding system)', async () => {
    mockOpenAIResponse(JSON.stringify({ response: 'Follow-up answer' }))
    const history = [
      { role: 'user' as const, content: 'First question' },
      { role: 'assistant' as const, content: 'First answer' },
    ]
    await askTrainer('Second question', [], profile, 'sk-test', 'openai', {
      conversationHistory: history,
    })
    const messages = (mockCreate.mock.calls[0][0] as { messages: Array<{ role: string; content: string }> }).messages
    // system + 2 history messages + 1 new user message
    expect(messages).toHaveLength(4)
    expect(messages[1]).toEqual({ role: 'user', content: 'First question' })
    expect(messages[2]).toEqual({ role: 'assistant', content: 'First answer' })
    expect(messages[3]).toEqual({ role: 'user', content: 'Second question' })
  })

  it('uses Gemini when provider is gemini', async () => {
    mockGeminiResponse(JSON.stringify({ response: 'Gemini answer' }))
    const result = await askTrainer('Test?', [], profile, 'gemini-key', 'gemini')
    expect(result.response).toBe('Gemini answer')
    expect(mockCreate).not.toHaveBeenCalled()
  })

  it('returns planUpdates when AI includes them', async () => {
    const tomorrow = new Date(Date.now() + 86400000).toISOString().split('T')[0]
    mockOpenAIResponse(
      JSON.stringify({
        response: 'Sure, I have swapped your interval session for an easy ride.',
        planUpdates: [
          { date: tomorrow, workoutType: 'recovery', title: 'Easy Recovery Ride', description: 'Light spin', durationMinutes: 45 },
        ],
      })
    )
    const result = await askTrainer('Can you swap tomorrow to an easy ride?', [], profile, 'sk-test')
    expect(result.planUpdates).toHaveLength(1)
    expect(result.planUpdates![0].date).toBe(tomorrow)
    expect(result.planUpdates![0].workoutType).toBe('recovery')
  })

  it('returns empty planUpdates when AI does not include them', async () => {
    mockOpenAIResponse(JSON.stringify({ response: 'Just a normal answer.' }))
    const result = await askTrainer('How should I train?', [], profile, 'sk-test')
    expect(result.planUpdates).toBeUndefined()
  })
})

describe('updateCoachMemory', () => {
  it('returns the updated memory text from OpenAI', async () => {
    mockOpenAIResponse('Athlete prefers morning rides.')
    const result = await updateCoachMemory('', 'I like riding in the morning', 'Got it!', 'sk-test')
    expect(result).toBe('Athlete prefers morning rides.')
  })

  it('includes current memory in the prompt so AI can append to it', async () => {
    mockOpenAIResponse('Updated notes')
    const existing = 'Has knee issues.'
    await updateCoachMemory(existing, 'I also have a race in June', 'Noted!', 'sk-test')
    const userMsg: string = (mockCreate.mock.calls[0][0] as { messages: Array<{ role: string; content: string }> }).messages[1].content
    expect(userMsg).toContain('Has knee issues.')
  })

  it('uses Gemini when provider is gemini', async () => {
    mockGeminiResponse('Gemini memory')
    const result = await updateCoachMemory('', 'message', 'response', 'gk', 'gemini')
    expect(result).toBe('Gemini memory')
    expect(mockCreate).not.toHaveBeenCalled()
  })
})

describe('generateTrainingPlan', () => {
  it('parses and returns the plan array from OpenAI JSON response', async () => {
    const planDay = makeDay('2024-05-01')
    mockOpenAIResponse(JSON.stringify({ plan: [planDay] }))
    const result = await generateTrainingPlan(profile, 'sk-test')
    expect(result).toHaveLength(1)
    expect(result[0].date).toBe('2024-05-01')
  })

  it('handles JSON wrapped in a markdown code fence', async () => {
    const planDay = makeDay('2024-05-01')
    mockOpenAIResponse('```json\n' + JSON.stringify({ plan: [planDay] }) + '\n```')
    const result = await generateTrainingPlan(profile, 'sk-test')
    expect(result[0].date).toBe('2024-05-01')
  })

  it('repairs malformed JSON where the model embeds units in numeric values', async () => {
    // Simulate what gpt-4o-mini / Gemini sometimes produces: "durationMinutes": 90 minutes
    const malformed = `{"plan":[{"date":"2024-05-01","workoutType":"endurance","title":"Easy Ride","description":"Z2","durationMinutes":90 minutes,"completed":false}]}`
    mockOpenAIResponse(malformed)
    const result = await generateTrainingPlan(profile, 'sk-test')
    expect(result[0].durationMinutes).toBe(90)
  })
})

describe('adaptTrainingPlan', () => {
  it('merges updated incomplete days and keeps completed days intact', async () => {
    const completedDay = { ...makeDay('2024-05-01'), completed: true }
    const incompleteDay = makeDay('2024-05-02')
    const updatedIncomplete = { ...incompleteDay, durationMinutes: 45 }
    const plan = [completedDay, incompleteDay]

    mockOpenAIResponse(JSON.stringify({ updatedDays: [updatedIncomplete] }))
    const result = await adaptTrainingPlan(plan, [], profile, 'sk-test')

    expect(result[0]).toEqual(completedDay) // completed day unchanged
    expect(result[1].durationMinutes).toBe(45) // incomplete day updated
  })
})

describe('rateCompletedWorkout', () => {
  const completedDay = {
    ...makeDay('2024-05-01', true),
    feedback: {
      actualDurationMinutes: 85,
      averagePower: 210,
      perceivedEffort: 3 as const,
      notes: 'Felt good',
      completedAt: '2024-05-01T10:00:00Z',
    },
  }

  it('returns the OpenAI rating text', async () => {
    mockOpenAIResponse('Great session! You matched the plan well.')
    const result = await rateCompletedWorkout(completedDay, profile, 'sk-test')
    expect(result).toBe('Great session! You matched the plan well.')
  })

  it('returns empty string when feedback is absent', async () => {
    const dayWithoutFeedback = makeDay('2024-05-01')
    const result = await rateCompletedWorkout(dayWithoutFeedback, profile, 'sk-test')
    expect(result).toBe('')
    expect(mockCreate).not.toHaveBeenCalled()
  })

  it('uses Gemini when provider is gemini', async () => {
    mockGeminiResponse('Gemini rating')
    const result = await rateCompletedWorkout(completedDay, profile, 'gemini-key', 'gemini')
    expect(result).toBe('Gemini rating')
    expect(mockCreate).not.toHaveBeenCalled()
  })
})
