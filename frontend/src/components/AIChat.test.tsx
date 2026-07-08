import { StrictMode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import AIChat from './AIChat'
import { useAppStore } from '../store/useAppStore'
import type { UserProfile } from '../store/useAppStore'

const {
  mockAskTrainer,
  mockFetchCoachMemory,
  mockFetchCurrentUser,
  mockClearChatHistoryRemote,
} = vi.hoisted(() => ({
  mockAskTrainer: vi.fn(),
  mockFetchCoachMemory: vi.fn(),
  mockFetchCurrentUser: vi.fn(),
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
  fetchCurrentUser: mockFetchCurrentUser,
  clearChatHistoryRemote: mockClearChatHistoryRemote,
}))

const baseProfile: UserProfile = {
  name: 'Alice',
  email: 'alice@example.com',
  bikeType: 'road',
  trainingGoal: 'general_fitness',
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
  mockFetchCurrentUser.mockResolvedValue({ profile: baseProfile })
  mockClearChatHistoryRemote.mockResolvedValue(undefined)
})

describe('AIChat', () => {
  it('shows a welcome message when there is no chat history', () => {
    setupStore()
    render(<AIChat />)
    expect(screen.getByText(/Hey, good to see you/i)).toBeInTheDocument()
    expect(screen.getByText(/How are you feeling about training today/i)).toBeInTheDocument()
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
    expect(screen.getByText(/How are you feeling about it/i)).toBeInTheDocument()
  })

  it('shows the newest exchange first while keeping each question above its answer', () => {
    setupStore({
      chatHistory: [
        { role: 'user', content: 'Older question', timestamp: '2026-06-15T10:00:00.000Z' },
        { role: 'assistant', content: 'Older answer', timestamp: '2026-06-15T10:00:01.000Z' },
        { role: 'user', content: 'Newest question', timestamp: '2026-06-15T10:01:00.000Z' },
        { role: 'assistant', content: 'Newest answer', timestamp: '2026-06-15T10:01:01.000Z' },
      ],
    })
    render(<AIChat />)

    const newestQuestion = screen.getByText('Newest question')
    const newestAnswer = screen.getByText('Newest answer')
    const olderQuestion = screen.getByText('Older question')

    expect(newestQuestion.compareDocumentPosition(newestAnswer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(newestAnswer.compareDocumentPosition(olderQuestion) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('starts with four exchanges and appends earlier history near the scroll end', () => {
    setupStore({
      chatHistory: Array.from({ length: 6 }).flatMap((_, index) => [
        {
          role: 'user' as const,
          content: `Question ${index + 1}`,
          timestamp: `2026-06-15T10:0${index}:00.000Z`,
        },
        {
          role: 'assistant' as const,
          content: `Answer ${index + 1}`,
          timestamp: `2026-06-15T10:0${index}:01.000Z`,
        },
      ]),
    })
    render(<AIChat />)

    expect(screen.getByText('Question 6')).toBeInTheDocument()
    expect(screen.getByText('Answer 6')).toBeInTheDocument()
    expect(screen.getByText('Question 3')).toBeInTheDocument()
    expect(screen.queryByText('Question 2')).not.toBeInTheDocument()
    expect(screen.queryByText('Question 1')).not.toBeInTheDocument()
    expect(screen.getByTestId('older-history-fade')).toBeInTheDocument()

    const messages = screen.getByLabelText('Coach chat messages')
    Object.defineProperties(messages, {
      clientHeight: { configurable: true, value: 400 },
      scrollHeight: { configurable: true, value: 800 },
      scrollTop: { configurable: true, value: 340 },
    })
    fireEvent.scroll(messages)

    expect(screen.getByText('Question 6')).toBeInTheDocument()
    expect(screen.getByText('Answer 6')).toBeInTheDocument()
    expect(screen.getByText('Question 3')).toBeInTheDocument()
    expect(screen.getByText('Question 2')).toBeInTheDocument()
    expect(screen.getByText('Answer 1')).toBeInTheDocument()
    expect(screen.queryByTestId('older-history-fade')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Show earlier messages/i })).not.toBeInTheDocument()
  })

  it('keeps earlier history hidden until the athlete wheels toward older messages', () => {
    setupStore({
      chatHistory: Array.from({ length: 6 }).flatMap((_, index) => [
        {
          role: 'user' as const,
          content: `Short question ${index + 1}`,
          timestamp: `2026-06-15T10:0${index}:00.000Z`,
        },
        {
          role: 'assistant' as const,
          content: `Short answer ${index + 1}`,
          timestamp: `2026-06-15T10:0${index}:01.000Z`,
        },
      ]),
    })
    render(<AIChat />)

    expect(screen.getByText('Short question 6')).toBeInTheDocument()
    expect(screen.getByText('Short question 3')).toBeInTheDocument()
    expect(screen.queryByText('Short question 2')).not.toBeInTheDocument()
    expect(screen.queryByText('Short question 1')).not.toBeInTheDocument()
    expect(screen.getByTestId('older-history-fade')).toBeInTheDocument()

    fireEvent.wheel(screen.getByLabelText('Coach chat messages'), { deltaY: 120 })

    expect(screen.getByText('Short question 6')).toBeInTheDocument()
    expect(screen.getByText('Short question 2')).toBeInTheDocument()
    expect(screen.getByText('Short question 1')).toBeInTheDocument()
    expect(screen.queryByTestId('older-history-fade')).not.toBeInTheDocument()
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

  it('shows a "Why this advice?" disclosure with both rationale layers', async () => {
    mockAskTrainer.mockResolvedValue({
      response: 'On the numbers a ride is fine, but knowing you I would rest.',
      physiologyRationale: 'fresh enough for an easy ride',
      contextRationale: 'history of overreaching favours rest',
    })
    setupStore()
    render(<AIChat />)

    const input = screen.getByPlaceholderText('Ask your coach...')
    await userEvent.type(input, 'Can I ride today?')
    await userEvent.click(screen.getByRole('button', { name: /Send message/i }))

    await waitFor(() => {
      expect(screen.getByText('Why this advice?')).toBeInTheDocument()
    })
    expect(screen.getByText('fresh enough for an easy ride')).toBeInTheDocument()
    expect(screen.getByText('history of overreaching favours rest')).toBeInTheDocument()
    // Layers are labelled with the shared knowledge-source vocabulary (#377):
    // the physiology read is a coach inference, the context read a personal
    // observation — the same labels the readiness card uses.
    expect(screen.getByText('Coach inference')).toBeInTheDocument()
    expect(screen.getByText('Personal observation')).toBeInTheDocument()
  })

  it('omits the rationale disclosure when no rationale is returned', async () => {
    mockAskTrainer.mockResolvedValue({ response: 'Easy spin today.' })
    setupStore()
    render(<AIChat />)

    const input = screen.getByPlaceholderText('Ask your coach...')
    await userEvent.type(input, 'What should I do?')
    await userEvent.click(screen.getByRole('button', { name: /Send message/i }))

    await waitFor(() => {
      expect(screen.getByText('Easy spin today.')).toBeInTheDocument()
    })
    expect(screen.queryByText('Why this advice?')).toBeNull()
  })

  it('only auto-sends a pending coach message once in StrictMode', async () => {
    mockAskTrainer.mockResolvedValue({ response: 'Let me unpack that.' })
    setupStore({ pendingCoachMessage: 'Why was my ride so hard?' })

    render(
      <StrictMode>
        <AIChat />
      </StrictMode>
    )

    await waitFor(() => {
      expect(mockAskTrainer).toHaveBeenCalledTimes(1)
    })
    expect(mockAskTrainer).toHaveBeenCalledWith('Why was my ride so hard?', 'token-123', expect.any(Object))
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
    expect(screen.getByRole('button', { name: /Retry coach response/i })).toBeEnabled()
  })

  it('retries the failed coach response from the failed message bubble', async () => {
    mockAskTrainer
      .mockRejectedValueOnce(new Error('Network error'))
      .mockResolvedValueOnce({ response: 'Recovered response.' })
    setupStore()
    render(<AIChat />)

    expect(screen.queryByRole('button', { name: /Retry coach response/i })).not.toBeInTheDocument()

    const input = screen.getByPlaceholderText('Ask your coach...')
    await userEvent.type(input, 'Question')
    await userEvent.click(screen.getByRole('button', { name: /Send message/i }))

    await waitFor(() => {
      expect(screen.getByText(/something went wrong/i)).toBeInTheDocument()
    })

    await userEvent.click(screen.getByRole('button', { name: /Retry coach response/i }))

    await waitFor(() => {
      expect(mockAskTrainer).toHaveBeenNthCalledWith(1, 'Question', 'token-123', expect.any(Object))
      expect(mockAskTrainer).toHaveBeenNthCalledWith(2, 'Question', 'token-123', expect.any(Object))
      expect(screen.getByText('Recovered response.')).toBeInTheDocument()
    })

    expect(screen.getAllByText('Question')).toHaveLength(1)
    expect(screen.queryByRole('button', { name: /Retry coach response/i })).not.toBeInTheDocument()
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

  it('replaces local training plan with updatedPlan returned by the backend', async () => {
    const tomorrow = new Date(Date.now() + 86400000).toISOString().split('T')[0]
    const dayAfter = new Date(Date.now() + 2 * 86400000).toISOString().split('T')[0]
    mockAskTrainer.mockResolvedValue({
      response: 'I persisted the recovery adjustment.',
      planUpdates: [
        { date: tomorrow, workoutType: 'recovery', title: 'Recovery Ride', description: 'Easy spin', durationMinutes: 45 },
      ],
      updatedPlan: [
        { date: tomorrow, workoutType: 'recovery', title: 'Recovery Ride', description: 'Easy spin', durationMinutes: 45 },
        { date: dayAfter, workoutType: 'endurance', title: 'Server Persisted Endurance', description: 'Z2 ride', durationMinutes: 75 },
      ],
    })
    setupStore({
      trainingPlan: [
        { date: tomorrow, workoutType: 'intervals', title: 'Hard Intervals', description: '5x5min', durationMinutes: 60 },
      ],
    })
    render(<AIChat />)

    const input = screen.getByPlaceholderText('Ask your coach...')
    await userEvent.type(input, 'Can you make tomorrow easier?')
    await userEvent.click(screen.getByRole('button', { name: /Send message/i }))

    await waitFor(() => {
      expect(screen.getByText(/Training plan updated: 1 day modified/i)).toBeInTheDocument()
    })

    const state = useAppStore.getState()
    expect(state.trainingPlan).toHaveLength(2)
    expect(state.trainingPlan[0].workoutType).toBe('recovery')
    expect(state.trainingPlan[1].title).toBe('Server Persisted Endurance')
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

  it('shows science sources when the AI returns them', async () => {
    mockAskTrainer.mockResolvedValue({
      response: 'Zone 2 training builds your aerobic base.',
      sources: [
        { title: 'Polarized Training Study', doi: '10.1/test', sourceType: 'paper' },
        { title: 'Power Zones', sourceType: 'seed' },
      ],
    })
    setupStore()
    render(<AIChat />)

    const input = screen.getByPlaceholderText('Ask your coach...')
    await userEvent.type(input, 'What zone should I train in?')
    await userEvent.click(screen.getByRole('button', { name: /Send message/i }))

    await waitFor(() => {
      expect(screen.getByText('Zone 2 training builds your aerobic base.')).toBeInTheDocument()
      expect(screen.getByText('Sources')).toBeInTheDocument()
      expect(screen.getByText('Polarized Training Study')).toBeInTheDocument()
      expect(screen.getByText('Power Zones')).toBeInTheDocument()
    })
  })

  it('does not show sources section when no sources are returned', async () => {
    mockAskTrainer.mockResolvedValue({
      response: 'Rest up tomorrow.',
    })
    setupStore()
    render(<AIChat />)

    const input = screen.getByPlaceholderText('Ask your coach...')
    await userEvent.type(input, 'Should I rest?')
    await userEvent.click(screen.getByRole('button', { name: /Send message/i }))

    await waitFor(() => {
      expect(screen.getByText('Rest up tomorrow.')).toBeInTheDocument()
    })
    expect(screen.queryByText('Sources')).not.toBeInTheDocument()
  })

  it('inserts a new line on Shift+Enter without sending the message', async () => {
    setupStore()
    render(<AIChat />)

    const input = screen.getByPlaceholderText('Ask your coach...')
    await userEvent.type(input, 'First line')
    await userEvent.keyboard('{Shift>}{Enter}{/Shift}')
    await userEvent.type(input, 'Second line')

    expect(input).toHaveValue('First line\nSecond line')
    expect(mockAskTrainer).not.toHaveBeenCalled()
  })

  it('renders bold and italic markdown in assistant messages', async () => {
    mockAskTrainer.mockResolvedValue({ response: 'Try **harder** or *easier* next time.' })
    setupStore()
    render(<AIChat />)

    const input = screen.getByPlaceholderText('Ask your coach...')
    await userEvent.type(input, 'How hard?')
    await userEvent.click(screen.getByRole('button', { name: /Send message/i }))

    await waitFor(() => {
      expect(screen.getByText('harder')).toBeInTheDocument()
      expect(screen.getByText('easier')).toBeInTheDocument()
    })
    // Bold text should be wrapped in <strong>, italic in <em>
    expect(document.querySelector('strong')?.textContent).toBe('harder')
    expect(document.querySelector('em')?.textContent).toBe('easier')
  })

  it('shows a stale-refresh warning when post-chat refresh fails after one retry', async () => {
    mockAskTrainer.mockResolvedValue({ response: 'Good question!' })
    // Both fetches fail on every attempt (two retries each = four total calls each)
    mockFetchCoachMemory.mockRejectedValue(new Error('network error'))
    mockFetchCurrentUser.mockRejectedValue(new Error('network error'))
    setupStore()
    render(<AIChat />)

    const input = screen.getByPlaceholderText('Ask your coach...')
    await userEvent.type(input, 'How do I train?')
    await userEvent.click(screen.getByRole('button', { name: /Send message/i }))

    await waitFor(() => {
      expect(screen.getByText(/Couldn't refresh coach state/i)).toBeInTheDocument()
    })
  })

  it('clears the stale-refresh warning after a subsequent successful exchange', async () => {
    mockAskTrainer.mockResolvedValue({ response: 'Good question!' })
    mockFetchCoachMemory
      .mockRejectedValueOnce(new Error('fail'))
      .mockRejectedValueOnce(new Error('fail'))
      .mockResolvedValue('fresh memory')
    mockFetchCurrentUser.mockResolvedValue({ profile: { name: 'Alice', email: 'alice@example.com', bikeType: 'road', trainingGoal: 'general_fitness', followsTrainingPlan: true, fitnessLevel: 'intermediate' } })
    setupStore()
    render(<AIChat />)

    // First message → warning appears
    const input = screen.getByPlaceholderText('Ask your coach...')
    await userEvent.type(input, 'First question')
    await userEvent.click(screen.getByRole('button', { name: /Send message/i }))
    await waitFor(() => expect(screen.getByText(/Couldn't refresh coach state/i)).toBeInTheDocument())

    // Second message → refresh succeeds → warning gone
    await userEvent.type(input, 'Second question')
    await userEvent.click(screen.getByRole('button', { name: /Send message/i }))
    await waitFor(() => expect(screen.queryByText(/Couldn't refresh coach state/i)).not.toBeInTheDocument())
  })

})
