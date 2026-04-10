import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import AIChat from '../components/AIChat'
import { useAppStore } from '../store/useAppStore'
import type { UserProfile } from '../store/useAppStore'

// ─── Hoisted mocks ────────────────────────────────────────────────────────────

const { mockAskTrainer, mockUpdateCoachMemory } = vi.hoisted(() => ({
  mockAskTrainer: vi.fn(),
  mockUpdateCoachMemory: vi.fn(),
}))

vi.mock('../services/ai', async (importOriginal) => {
  const original = await importOriginal<typeof import('../services/ai')>()
  return {
    ...original,
    askTrainer: mockAskTrainer,
    updateCoachMemory: mockUpdateCoachMemory,
  }
})

// ─── Helpers ──────────────────────────────────────────────────────────────────

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
    userProfile: baseProfile,
    aiApiKey: 'sk-test',
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
  mockUpdateCoachMemory.mockResolvedValue('')
})

// ─── Tests ───────────────────────────────────────────────────────────────────

describe('AIChat', () => {
  it('shows a welcome message when there is no chat history', () => {
    setupStore()
    render(<AIChat />)
    expect(
      screen.getByText(/Hi! I'm your AI cycling coach/i)
    ).toBeInTheDocument()
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

  it('renders persisted chat history instead of welcome message', () => {
    setupStore({
      chatHistory: [
        { role: 'user', content: 'What should I eat before a ride?', timestamp: '' },
        { role: 'assistant', content: 'Eat a banana 30 min before.', timestamp: '' },
      ],
    })
    render(<AIChat />)
    expect(screen.getByText('What should I eat before a ride?')).toBeInTheDocument()
    expect(screen.getByText('Eat a banana 30 min before.')).toBeInTheDocument()
  })

  it('send button is disabled when input is empty', () => {
    setupStore()
    render(<AIChat />)
    const sendButton = screen.getByRole('button', { name: /Send message/i }) // Send icon button
    // Input is empty by default, button should be disabled
    const input = screen.getByPlaceholderText('Ask your coach...')
    expect(input).toHaveValue('')
    expect(sendButton).toBeDisabled()
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

  it('shows an API key error when no key is configured', async () => {
    setupStore({ aiApiKey: '' })
    render(<AIChat />)

    const input = screen.getByPlaceholderText('Ask your coach...')
    await userEvent.type(input, 'Hello?')
    await userEvent.click(screen.getByRole('button', { name: /Send message/i }))

    await waitFor(() => {
      expect(screen.getByText(/Please add your AI provider API key/i)).toBeInTheDocument()
    })
    expect(mockAskTrainer).not.toHaveBeenCalled()
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

  it('shows the clear history button when chat history is non-empty', () => {
    setupStore({
      chatHistory: [{ role: 'user', content: 'Hi', timestamp: '' }],
    })
    render(<AIChat />)
    expect(screen.getByTitle('Clear chat history')).toBeInTheDocument()
  })

  it('hides the clear history button when chat history is empty', () => {
    setupStore({ chatHistory: [] })
    render(<AIChat />)
    expect(screen.queryByTitle('Clear chat history')).not.toBeInTheDocument()
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
  })

  it('shows the Memory button when coachMemory is non-empty', () => {
    setupStore({ coachMemory: 'Athlete has knee issues.' })
    render(<AIChat />)
    expect(screen.getByTitle('Coach memory')).toBeInTheDocument()
  })

  it('hides the Memory button when coachMemory is empty', () => {
    setupStore({ coachMemory: '' })
    render(<AIChat />)
    expect(screen.queryByTitle('Coach memory')).not.toBeInTheDocument()
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

  it('calls updateCoachMemory in background after a successful exchange', async () => {
    mockAskTrainer.mockResolvedValue({ response: 'Great question!' })
    mockUpdateCoachMemory.mockResolvedValue('Updated memory')
    setupStore()
    render(<AIChat />)

    const input = screen.getByPlaceholderText('Ask your coach...')
    await userEvent.type(input, 'How do I improve?')
    await userEvent.click(screen.getByRole('button', { name: /Send message/i }))

    await waitFor(() => {
      expect(mockUpdateCoachMemory).toHaveBeenCalledWith(
        '', // initial empty memory
        'How do I improve?',
        'Great question!',
        'sk-test',
        'openai'
      )
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

    // Verify the store was updated
    const state = useAppStore.getState()
    const updatedDay = state.trainingPlan.find((d) => d.date === tomorrow)
    expect(updatedDay?.workoutType).toBe('recovery')
  })
})
