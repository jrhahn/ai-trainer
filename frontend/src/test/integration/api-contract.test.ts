/**
 * Integration tests: TypeScript frontend service functions → real FastAPI backend.
 *
 * These tests call the ACTUAL service functions (no vi.mock() anywhere).
 * apiFetch → native Node.js fetch → real HTTP → real SQLite-backed FastAPI process.
 *
 * AI endpoints (generateTrainingPlan, analyseStravaActivities, etc.) are deliberately
 * excluded here because they require external AI API calls.  Those are covered by the
 * backend contract tests (backend/tests/test_contract.py) with a mocked AI service.
 *
 * Endpoints exercised:
 *   POST /auth/register       via register()
 *   POST /auth/login          via login()
 *   GET  /users/me            via fetchCurrentUser()
 *   PUT  /users/me            via updateCurrentUser()
 *   GET  /users/me/plan       via fetchTrainingPlan()
 *   PUT  /users/me/plan       via saveTrainingPlan()
 *   GET  /users/me/workouts   via fetchWorkoutLogs()
 *   POST /users/me/workouts/  via saveWorkoutLog()
 *   GET  /users/me/chat       via fetchChatHistory()
 *   POST /users/me/chat       via saveChatMessage()
 *   DELETE /users/me/chat     via clearChatHistoryRemote()
 *   GET  /users/me/coach-memory  via fetchCoachMemory()
 *   PUT  /users/me/coach-memory  via saveCoachMemoryRemote()
 *   DELETE /users/me          via deleteCurrentUser()
 */

import { beforeAll, describe, expect, it } from 'vitest'
import { login, register } from '../../services/auth'
import {
  clearChatHistoryRemote,
  deleteCurrentUser,
  fetchChatHistory,
  fetchCoachMemory,
  fetchCurrentUser,
  fetchTrainingPlan,
  fetchWorkoutLogs,
  saveChatMessage,
  saveCoachMemoryRemote,
  saveTrainingPlan,
  saveWorkoutLog,
  updateCurrentUser,
} from '../../services/user'
import type { TrainingDay } from '../../store/useAppStore'

/** Generate a unique email per test so tests are independent. */
function uniqueEmail(label: string): string {
  return `${label}-${Date.now()}-${Math.random().toString(36).slice(2)}@example.com`
}

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

describe('auth service ↔ backend', () => {
  it('register returns a JWT string for a new user', async () => {
    const token = await register('Test Rider', uniqueEmail('reg'), 'Str0ng!Pass')
    expect(typeof token).toBe('string')
    expect(token!.length).toBeGreaterThan(10)
  })

  it('login returns a JWT string for a registered user', async () => {
    const email = uniqueEmail('login')
    await register('Login Rider', email, 'Str0ng!Pass')
    const token = await login(email, 'Str0ng!Pass')
    expect(typeof token).toBe('string')
    expect(token.length).toBeGreaterThan(10)
  })

  it('register rejects a duplicate email', async () => {
    const email = uniqueEmail('dup')
    await register('First', email, 'Str0ng!Pass')
    await expect(register('Second', email, 'Str0ng!Pass')).rejects.toThrow()
  })

  it('login rejects wrong credentials', async () => {
    const email = uniqueEmail('wrongpw')
    await register('WrongPw Rider', email, 'Str0ng!Pass')
    await expect(login(email, 'wrongpassword')).rejects.toThrow()
  })
})

// ---------------------------------------------------------------------------
// User profile
// ---------------------------------------------------------------------------

describe('user profile service ↔ backend', () => {
  let token: string

  beforeAll(async () => {
    const email = uniqueEmail('profile')
    token = (await register('Alice Rider', email, 'Str0ng!Pass'))!
  })

  it('fetchCurrentUser returns the correct initial shape', async () => {
    const data = await fetchCurrentUser(token)
    expect(data.profile.email).toContain('@example.com')
    expect(data.isOnboarded).toBe(false)
    expect(data.stravaAnalysisComplete).toBe(false)
    expect(data.riderAssessment).toBeNull()
    expect(data.stravaConnection).toBeNull()
    expect(data.aiProvider).toBe('openai')
  })

  it('updateCurrentUser persists camelCase profile fields', async () => {
    const data = await updateCurrentUser(token, {
      name: 'Alice Updated',
      bikeType: 'gravel',
      trainingGoal: 'general_fitness',
      fitnessLevel: 'advanced',
      currentFTP: 310,
      maxHeartRate: 188,
      followsTrainingPlan: true,
      isOnboarded: true,
      stravaAnalysisComplete: false,
    })
    expect(data.profile.bikeType).toBe('gravel')
    expect(data.profile.trainingGoal).toBe('general_fitness')
    expect(data.profile.fitnessLevel).toBe('advanced')
    expect(data.profile.currentFTP).toBe(310)
    expect(data.profile.maxHeartRate).toBe(188)
    expect(data.profile.followsTrainingPlan).toBe(true)
    expect(data.isOnboarded).toBe(true)
  })

  it('subsequent fetchCurrentUser reflects the update', async () => {
    const data = await fetchCurrentUser(token)
    expect(data.profile.bikeType).toBe('gravel')
    expect(data.isOnboarded).toBe(true)
    expect(data.profile.currentFTP).toBe(310)
  })

  it('deleteCurrentUser removes the account (subsequent request fails)', async () => {
    const email = uniqueEmail('deleteme')
    const delToken = (await register('Del Rider', email, 'Str0ng!Pass'))!
    await deleteCurrentUser(delToken)
    await expect(fetchCurrentUser(delToken)).rejects.toThrow()
  })
})

// ---------------------------------------------------------------------------
// Training plan
// ---------------------------------------------------------------------------

describe('training plan service ↔ backend', () => {
  let token: string

  beforeAll(async () => {
    const email = uniqueEmail('plan')
    token = (await register('Plan Rider', email, 'Str0ng!Pass'))!
  })

  it('fetchTrainingPlan returns an empty array for a new user', async () => {
    const plan = await fetchTrainingPlan(token)
    expect(plan).toEqual([])
  })

  it('saveTrainingPlan persists the plan', async () => {
    const planDays: TrainingDay[] = [
      {
        date: '2026-05-01',
        workoutType: 'endurance',
        title: 'Z2 Ride',
        description: 'Easy aerobic ride',
        durationMinutes: 90,
      },
      {
        date: '2026-05-02',
        workoutType: 'rest',
        title: 'Rest Day',
        description: 'Full rest',
        durationMinutes: 0,
      },
    ]
    const saved = await saveTrainingPlan(token, planDays)
    expect(saved).toEqual(planDays)
  })

  it('fetchTrainingPlan retrieves the saved plan', async () => {
    const plan = await fetchTrainingPlan(token)
    expect(plan).toHaveLength(2)
    expect(plan[0].date).toBe('2026-05-01')
    expect(plan[0].workoutType).toBe('endurance')
    expect(plan[1].workoutType).toBe('rest')
  })

  it('saveTrainingPlan replaces the plan on a second call', async () => {
    const newPlan: TrainingDay[] = [
      {
        date: '2026-06-01',
        workoutType: 'intervals',
        title: 'VO2 Max',
        description: '5×4 min at 110% FTP',
        durationMinutes: 60,
      },
    ]
    await saveTrainingPlan(token, newPlan)
    const plan = await fetchTrainingPlan(token)
    expect(plan).toHaveLength(1)
    expect(plan[0].workoutType).toBe('intervals')
  })
})

// ---------------------------------------------------------------------------
// Workout logs
// ---------------------------------------------------------------------------

describe('workout log service ↔ backend', () => {
  let token: string

  beforeAll(async () => {
    const email = uniqueEmail('workout')
    token = (await register('Workout Rider', email, 'Str0ng!Pass'))!
  })

  it('fetchWorkoutLogs returns empty object for a new user', async () => {
    const logs = await fetchWorkoutLogs(token)
    expect(logs).toEqual({})
  })

  it('saveWorkoutLog persists camelCase feedback fields', async () => {
    await saveWorkoutLog(token, '2026-05-01', {
      actualDurationMinutes: 75,
      averagePower: 215,
      averageHeartRate: 152,
      peakPower: 380,
      perceivedEffort: 3,
      notes: 'Felt strong today',
      completedAt: '2026-05-01T10:30:00Z',
    })
  })

  it('fetchWorkoutLogs returns the saved log keyed by date', async () => {
    const logs = await fetchWorkoutLogs(token)
    expect(logs['2026-05-01']).toBeDefined()
    const log = logs['2026-05-01']
    expect(log.actualDurationMinutes).toBe(75)
    expect(log.averagePower).toBe(215)
    expect(log.averageHeartRate).toBe(152)
    expect(log.peakPower).toBe(380)
    expect(log.perceivedEffort).toBe(3)
    expect(log.notes).toBe('Felt strong today')
    expect(log.completedAt).toBe('2026-05-01T10:30:00Z')
  })

  it('saveWorkoutLog updates an existing log for the same date', async () => {
    await saveWorkoutLog(token, '2026-05-01', {
      actualDurationMinutes: 80,
      perceivedEffort: 4,
      notes: 'Updated notes',
      completedAt: '2026-05-01T11:00:00Z',
    })
    const logs = await fetchWorkoutLogs(token)
    expect(logs['2026-05-01'].actualDurationMinutes).toBe(80)
    expect(logs['2026-05-01'].perceivedEffort).toBe(4)
    expect(logs['2026-05-01'].notes).toBe('Updated notes')
  })
})

// ---------------------------------------------------------------------------
// Chat history
// ---------------------------------------------------------------------------

describe('chat history service ↔ backend', () => {
  let token: string

  beforeAll(async () => {
    const email = uniqueEmail('chat')
    token = (await register('Chat Rider', email, 'Str0ng!Pass'))!
  })

  it('fetchChatHistory returns empty array for a new user', async () => {
    const history = await fetchChatHistory(token)
    expect(history).toEqual([])
  })

  it('saveChatMessage persists a message and returns the saved message', async () => {
    const saved = await saveChatMessage(token, {
      role: 'user',
      content: 'How should I train today?',
      timestamp: '2026-05-01T09:00:00Z',
    })
    expect(saved.role).toBe('user')
    expect(saved.content).toBe('How should I train today?')
    expect(saved.timestamp).toBe('2026-05-01T09:00:00Z')
  })

  it('fetchChatHistory returns all saved messages', async () => {
    await saveChatMessage(token, {
      role: 'assistant',
      content: 'Focus on zone 2 endurance today.',
      timestamp: '2026-05-01T09:01:00Z',
    })
    const history = await fetchChatHistory(token)
    expect(history).toHaveLength(2)
    expect(history[0].role).toBe('user')
    expect(history[1].role).toBe('assistant')
  })

  it('clearChatHistoryRemote removes all messages', async () => {
    await clearChatHistoryRemote(token)
    const history = await fetchChatHistory(token)
    expect(history).toEqual([])
  })
})

// ---------------------------------------------------------------------------
// Coach memory
// ---------------------------------------------------------------------------

describe('coach memory service ↔ backend', () => {
  let token: string

  beforeAll(async () => {
    const email = uniqueEmail('memory')
    token = (await register('Memory Rider', email, 'Str0ng!Pass'))!
  })

  it('fetchCoachMemory returns empty string for a new user', async () => {
    const memory = await fetchCoachMemory(token)
    expect(memory).toBe('')
  })

  it('saveCoachMemoryRemote persists memory', async () => {
    const saved = await saveCoachMemoryRemote(token, 'Athlete prefers morning rides.')
    expect(saved).toBe('Athlete prefers morning rides.')
  })

  it('fetchCoachMemory returns the saved value', async () => {
    const memory = await fetchCoachMemory(token)
    expect(memory).toBe('Athlete prefers morning rides.')
  })

  it('saveCoachMemoryRemote replaces memory on a second call', async () => {
    await saveCoachMemoryRemote(token, 'Updated: prefers evening rides now.')
    const memory = await fetchCoachMemory(token)
    expect(memory).toBe('Updated: prefers evening rides now.')
  })
})
