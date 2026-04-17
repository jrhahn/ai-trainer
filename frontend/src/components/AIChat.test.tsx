import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import AIChat from './AIChat'
import { useAppStore } from '../store/useAppStore'
import type { UserProfile } from '../store/useAppStore'

const {
  mockAskTrainer,
  mockFetchCoachMemory,
  mockClearChatHistoryRemote,
} = vi.hoisted(() => ({
  mockAskTrainer: vi.fn(),
  mockFetchCoachMemory: vi.fn(),
  mockClearChatHistoryRemote: vi.fn(),
}))

vi.mock('../services/ai', async (importOriginal) => {
  const original = await importOriginal<typeof import('../services/ai')>()
  return {
    ...original,
    askTrainer: mockAskTrainer,
  }
})

vi.mock('../services/user', () => ({
  fetchCoachMemory: mockFetchCoachMemory,
  clearChatHistoryRemote: mockClearChatHistoryRemote,
}))

const baseProfile: UserProfile = {
  name: 'Alice',
  email: 'alice@example.com',
  bikeType: 'road',
  trainingGoal: 'ftp_improvement',
  weeklyHours: 10,
  followsTrainingPlan: true,
  fitnessLevel: 'intermediate',
}

function setupStore(overrides: Partial<ReturnType<typeof useAppStore.getState>> = {}) {
  useAppStore.setState({
    authToken: 'token-123',
    userProfile: baseProfile,
    aiProvider: 'openai',
    trainingPlan: [],
    chatHistory: [],
    coachMemory: '',
    ...overrides,
  })
}

beforeEach(() => {
  useAppStore.getState().resetAll()
  vi.clearAllMocks()
  mockFetchCoachMemory.mockResolvedValue('')
  mockClearChatHistoryRemote.mockResolvedValue(undefined)
})

describe('AIChat', () => {
  it('shows a welcome message when there is no chat history', () => {
    setupStore()
    render(<AIChat />)
    expect(screen.getByText(/Hi! I'm your AI cycling coach/i)).toBeInTheDocument()
  })

  it('shows a workout-specific welcome message when contextWorkout is provided', () => {
    setupStore()
    render(
      <AIChat
        contextWorkout={{
          date: '2024-01-15',
          workoutType: 'intervals',
          title: 'VO2max Intervals',
          description: '5x4min at 120% FTP',
          durationMinutes: 60,
        }}
      />
    )
    expect(screen.getByText(/VO2max Intervals/)).toBeInTheDocument()
  })

  it('sends a message and displays the AI response', async () => {
    mockAskTrainer.mockResolvedValue({ response: 'Cadence of 90 rpm is ideal.' })
    setupStore()
    render(<AIChat />)

    const input = screen.getByPlaceholderText('Ask your coach...')
    await userEvent.type(input, 'What cadence should I target?')
    await userEvent.click(screen.getByRole('button', { name: /Send message/i }))

    await waitFor(() => {
      expect(screen.getByText('What cadence should I target?')).toBeInTheDocument()
      expect(screen.getByText('Cadence of 90 rpm is ideal.')).toBeInTheDocument()
    })
  })

  it('clears the input field after sending', async () => {
    mockAskTrainer.mockResolvedValue({ response: 'Answer.' })
    setupStore()
    render(<AIChat />)

    const input = screen.getByPlaceholderText('Ask your coach...')
    await userEvent.type(input, 'Test question')
    await userEvent.click(screen.getByRole('button', { name: /Send message/i }))

    await waitFor(() => {
      expect(input).toHaveValue('')
    })
  })

  it('shows error message when askTrainer throws', async () => {
    mockAskTrainer.mockRejectedValue(new Error('Network error'))
    setupStore()
    render(<AIChat />)

    const input = screen.getByPlaceholderText('Ask your coach...')
    await userEvent.type(input, 'Question')
    await userEvent.click(screen.getByRole('button', { name: /Send message/i }))

    await waitFor(() => {
      expect(screen.getByText(/something went wrong/i)).toBeInTheDocument()
    })
  })

  it('clears chat history when the clear button is clicked', async () => {
    setupStore({
      chatHistory: [{ role: 'user', content: 'Hi', timestamp: '' }],
    })
    render(<AIChat />)

    fireEvent.click(screen.getByTitle('Clear chat history'))

    await waitFor(() => {
      expect(useAppStore.getState().chatHistory).toEqual([])
    })
    expect(mockClearChatHistoryRemote).toHaveBeenCalledWith('token-123')
  })

  it('toggles the memory panel when the Memory button is clicked', async () => {
    setupStore({ coachMemory: 'Athlete has knee issues.' })
    render(<AIChat />)

    expect(screen.queryByText(/Coach's notes about you/)).not.toBeInTheDocument()

    fireEvent.click(screen.getByTitle('Coach memory'))
    expect(screen.getByText(/Coach's notes about you/)).toBeInTheDocument()
    expect(screen.getByText('Athlete has knee issues.')).toBeInTheDocument()

    fireEvent.click(screen.getByTitle('Coach memory'))
    expect(screen.queryByText(/Coach's notes about you/)).not.toBeInTheDocument()
  })

  it('re-fetches coach memory after a successful exchange', async () => {
    mockAskTrainer.mockResolvedValue({ response: 'Great question!' })
    mockFetchCoachMemory.mockResolvedValue('Updated memory')
    setupStore()
    render(<AIChat />)

    const input = screen.getByPlaceholderText('Ask your coach...')
    await userEvent.type(input, 'How do I improve?')
    await userEvent.click(screen.getByRole('button', { name: /Send message/i }))

    await waitFor(() => {
      expect(mockFetchCoachMemory).toHaveBeenCalledWith('token-123')
    })
    await waitFor(() => {
      expect(useAppStore.getState().coachMemory).toBe('Updated memory')
    })
  })

  it('shows a plan-updated badge when the AI returns planUpdates', async () => {
    const tomorrow = new Date(Date.now() + 86400000).toISOString().split('T')[0]
    mockAskTrainer.mockResolvedValue({
      response: 'I have swapped tomorrow to a recovery ride.',
      planUpdates: [
        { date: tomorrow, workoutType: 'recovery', title: 'Recovery Ride', description: 'Easy spin', durationMinutes: 45 },
      ],
    })
    setupStore({
      trainingPlan: [
        { date: tomorrow, workoutType: 'intervals', title: 'Hard Intervals', description: '5x5min', durationMinutes: 60 },
      ],
    })
    render(<AIChat />)

    const input = screen.getByPlaceholderText('Ask your coach...')
    await userEvent.type(input, 'Can you swap tomorrow to an easy ride?')
    await userEvent.click(screen.getByRole('button', { name: /Send message/i }))

    await waitFor(() => {
      expect(screen.getByText(/Training plan updated: 1 day modified/i)).toBeInTheDocument()
    })

    const state = useAppStore.getState()
    const updatedDay = state.trainingPlan.find((d) => d.date === tomorrow)
    expect(updatedDay?.workoutType).toBe('recovery')
  })

  it('passes contextWorkout to askTrainer when provided', async () => {
    mockAskTrainer.mockResolvedValue({ response: 'Good luck with the intervals!' })
    setupStore()
    const contextWorkout = {
      date: '2024-01-15',
      workoutType: 'intervals' as const,
      title: 'VO2max Intervals',
      description: '5x4min at 120% FTP',
      durationMinutes: 60,
    }
    render(<AIChat contextWorkout={contextWorkout} />)

    const input = screen.getByPlaceholderText('Ask your coach...')
    await userEvent.type(input, 'Can you make this easier?')
    await userEvent.click(screen.getByRole('button', { name: /Send message/i }))

    await waitFor(() => {
      expect(mockAskTrainer).toHaveBeenCalledWith(
        'Can you make this easier?',
        'token-123',
        expect.objectContaining({ contextWorkout })
      )
    })
  })
})
