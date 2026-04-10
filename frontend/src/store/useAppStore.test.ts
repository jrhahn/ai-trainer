import { describe, it, expect, beforeEach } from 'vitest'
import { useAppStore } from '../store/useAppStore'
import type { ChatMessage, WorkoutFeedback, TrainingDay } from '../store/useAppStore'

const mockFeedback: WorkoutFeedback = {
  actualDurationMinutes: 60,
  perceivedEffort: 3,
  notes: 'Felt good',
  completedAt: '2024-01-15T10:00:00Z',
}

const mockDay: TrainingDay = {
  date: '2024-01-15',
  workoutType: 'endurance',
  title: 'Easy Ride',
  description: '2h easy ride at Z2',
  durationMinutes: 120,
}

beforeEach(() => {
  useAppStore.getState().resetAll()
})

describe('chatHistory actions', () => {
  it('starts with an empty chat history', () => {
    expect(useAppStore.getState().chatHistory).toEqual([])
  })

  it('addChatMessage appends a message to the history', () => {
    const msg: ChatMessage = { role: 'user', content: 'Hello coach!', timestamp: '2024-01-15T09:00:00Z' }
    useAppStore.getState().addChatMessage(msg)
    expect(useAppStore.getState().chatHistory).toHaveLength(1)
    expect(useAppStore.getState().chatHistory[0]).toEqual(msg)
  })

  it('addChatMessage appends multiple messages in order', () => {
    const msg1: ChatMessage = { role: 'user', content: 'Question', timestamp: '2024-01-15T09:00:00Z' }
    const msg2: ChatMessage = { role: 'assistant', content: 'Answer', timestamp: '2024-01-15T09:01:00Z' }
    useAppStore.getState().addChatMessage(msg1)
    useAppStore.getState().addChatMessage(msg2)

    const history = useAppStore.getState().chatHistory
    expect(history).toHaveLength(2)
    expect(history[0]).toEqual(msg1)
    expect(history[1]).toEqual(msg2)
  })

  it('clearChatHistory resets history to empty array', () => {
    const msg: ChatMessage = { role: 'user', content: 'Hi', timestamp: '2024-01-15T09:00:00Z' }
    useAppStore.getState().addChatMessage(msg)
    expect(useAppStore.getState().chatHistory).toHaveLength(1)

    useAppStore.getState().clearChatHistory()
    expect(useAppStore.getState().chatHistory).toEqual([])
  })
})

describe('coachMemory actions', () => {
  it('starts with empty coach memory', () => {
    expect(useAppStore.getState().coachMemory).toBe('')
  })

  it('setCoachMemory stores the memory string', () => {
    const notes = 'Athlete prefers morning rides. Has knee sensitivity on high-cadence efforts.'
    useAppStore.getState().setCoachMemory(notes)
    expect(useAppStore.getState().coachMemory).toBe(notes)
  })

  it('setCoachMemory replaces previous memory', () => {
    useAppStore.getState().setCoachMemory('old notes')
    useAppStore.getState().setCoachMemory('new notes')
    expect(useAppStore.getState().coachMemory).toBe('new notes')
  })
})

describe('resetAll', () => {
  it('resets chatHistory and coachMemory to initial values', () => {
    const msg: ChatMessage = { role: 'user', content: 'Hi', timestamp: '2024-01-15T09:00:00Z' }
    useAppStore.getState().addChatMessage(msg)
    useAppStore.getState().setCoachMemory('some notes')

    useAppStore.getState().resetAll()

    expect(useAppStore.getState().chatHistory).toEqual([])
    expect(useAppStore.getState().coachMemory).toBe('')
  })

  it('resets all other state fields as well', () => {
    useAppStore.getState().setAiApiKey('test-key')
    useAppStore.getState().setOnboarded(true)
    useAppStore.getState().resetAll()

    const state = useAppStore.getState()
    expect(state.aiApiKey).toBe('')
    expect(state.isOnboarded).toBe(false)
    expect(state.trainingPlan).toEqual([])
  })
})

describe('logWorkout', () => {
  it('marks the day as completed and attaches feedback', () => {
    useAppStore.getState().setTrainingPlan([mockDay])
    useAppStore.getState().logWorkout('2024-01-15', mockFeedback)

    const day = useAppStore.getState().trainingPlan.find((d) => d.date === '2024-01-15')
    expect(day?.completed).toBe(true)
    expect(day?.feedback).toEqual(mockFeedback)
  })

  it('also stores feedback in workoutLogs keyed by date', () => {
    useAppStore.getState().setTrainingPlan([mockDay])
    useAppStore.getState().logWorkout('2024-01-15', mockFeedback)

    expect(useAppStore.getState().workoutLogs['2024-01-15']).toEqual(mockFeedback)
  })
})

describe('updateTrainingDay', () => {
  it('applies partial updates to the matching day', () => {
    useAppStore.getState().setTrainingPlan([mockDay])
    useAppStore.getState().updateTrainingDay('2024-01-15', { title: 'Updated Title', durationMinutes: 90 })

    const day = useAppStore.getState().trainingPlan.find((d) => d.date === '2024-01-15')
    expect(day?.title).toBe('Updated Title')
    expect(day?.durationMinutes).toBe(90)
    expect(day?.workoutType).toBe('endurance') // unchanged
  })
})
