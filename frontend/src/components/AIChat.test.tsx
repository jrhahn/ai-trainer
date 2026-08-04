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
  mockFetchPlanHistory,
  mockFetchAthleteOpenQuestions,
  mockFetchAthleteHypotheses,
  mockFetchValidationExperiments,
  mockFetchAthleteInquiries,
  mockAnswerAthleteInquiry,
  mockDismissAthleteInquiry,
} = vi.hoisted(() => ({
  mockAskTrainer: vi.fn(),
  mockFetchCoachMemory: vi.fn(),
  mockFetchCurrentUser: vi.fn(),
  mockClearChatHistoryRemote: vi.fn(),
  mockFetchPlanHistory: vi.fn(),
  mockFetchAthleteOpenQuestions: vi.fn(),
  mockFetchAthleteHypotheses: vi.fn(),
  mockFetchValidationExperiments: vi.fn(),
  mockFetchAthleteInquiries: vi.fn(),
  mockAnswerAthleteInquiry: vi.fn(),
  mockDismissAthleteInquiry: vi.fn(),
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
  fetchPlanHistory: mockFetchPlanHistory,
  fetchAthleteOpenQuestions: mockFetchAthleteOpenQuestions,
  fetchAthleteHypotheses: mockFetchAthleteHypotheses,
  fetchValidationExperiments: mockFetchValidationExperiments,
  fetchAthleteInquiries: mockFetchAthleteInquiries,
  answerAthleteInquiry: mockAnswerAthleteInquiry,
  dismissAthleteInquiry: mockDismissAthleteInquiry,
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
  mockFetchPlanHistory.mockResolvedValue([])
  mockFetchAthleteOpenQuestions.mockResolvedValue([])
  mockFetchAthleteHypotheses.mockResolvedValue([])
  mockFetchValidationExperiments.mockResolvedValue([])
  mockFetchAthleteInquiries.mockResolvedValue([])
  mockDismissAthleteInquiry.mockResolvedValue(undefined)
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

// ---------------------------------------------------------------------------
// Coach Timeline — interleaved system events (#418)
// ---------------------------------------------------------------------------

function planEntry(overrides: Record<string, unknown> = {}) {
  return {
    id: 'p1',
    date: '2026-06-15',
    source: 'coach_chat',
    applied: true,
    recordedAt: '2026-06-15T10:02:00.000Z',
    batchId: 'b1',
    oldDay: null,
    newDay: { workoutType: 'recovery', title: 'Recovery Ride' },
    ...overrides,
  }
}

function openQuestion(overrides: Record<string, unknown> = {}) {
  return {
    id: 'q1',
    question: 'Do you recover faster with an extra rest day?',
    category: 'recovery',
    evidence: '',
    needs: '',
    evidenceCount: 1,
    status: 'open',
    resolution: null,
    firstAskedAt: '2026-06-15T09:00:00.000Z',
    updatedAt: '2026-06-15T09:00:00.000Z',
    ...overrides,
  }
}

function hypothesis(overrides: Record<string, unknown> = {}) {
  return {
    id: 'h1',
    statement: 'Your threshold holds well in the heat.',
    category: 'physiology',
    rationale: 'Consistent hot-ride power.',
    confidence: 0.6,
    evidenceCount: 3,
    status: 'proposed',
    firstProposedAt: '2026-06-15T08:00:00.000Z',
    updatedAt: '2026-06-15T08:00:00.000Z',
    ...overrides,
  }
}

function experiment(overrides: Record<string, unknown> = {}) {
  return {
    id: 'e1',
    hypothesisId: null,
    question: 'Does a 20-minute warmup improve your VO2 efforts?',
    protocol: 'Add a long warmup before intervals for two weeks.',
    rationale: 'Testing readiness.',
    category: 'training',
    status: 'suggested',
    createdAt: '2026-06-15T07:00:00.000Z',
    updatedAt: '2026-06-15T07:00:00.000Z',
    ...overrides,
  }
}

describe('AIChat — Coach Timeline events', () => {
  it('shows an applied plan change as a run-labelled timeline entry', async () => {
    setupStore()
    mockFetchPlanHistory.mockResolvedValue([planEntry()])
    render(<AIChat />)

    expect(await screen.findByText('Coach chat')).toBeInTheDocument()
    expect(screen.getByText(/Added recovery — Recovery Ride/)).toBeInTheDocument()
  })

  it('surfaces open questions, proposed hypotheses and suggested experiments', async () => {
    setupStore()
    mockFetchAthleteOpenQuestions.mockResolvedValue([openQuestion()])
    mockFetchAthleteHypotheses.mockResolvedValue([hypothesis()])
    mockFetchValidationExperiments.mockResolvedValue([experiment()])
    render(<AIChat />)

    expect(await screen.findByText('Open question')).toBeInTheDocument()
    expect(screen.getByText('Do you recover faster with an extra rest day?')).toBeInTheDocument()
    expect(screen.getByText('Coach hypothesis')).toBeInTheDocument()
    expect(screen.getByText('Your threshold holds well in the heat.')).toBeInTheDocument()
    expect(screen.getByText('Suggested experiment')).toBeInTheDocument()
    expect(screen.getByText('Does a 20-minute warmup improve your VO2 efforts?')).toBeInTheDocument()
  })

  it('omits recommendations and plan changes that are no longer actionable', async () => {
    setupStore()
    mockFetchPlanHistory.mockResolvedValue([planEntry({ applied: false })])
    mockFetchAthleteOpenQuestions.mockResolvedValue([openQuestion({ status: 'answered' })])
    mockFetchAthleteHypotheses.mockResolvedValue([hypothesis({ status: 'refuted' })])
    mockFetchValidationExperiments.mockResolvedValue([experiment({ status: 'completed' })])
    render(<AIChat />)

    // Give the fetch effect a chance to resolve before asserting nothing rendered.
    await waitFor(() => expect(mockFetchPlanHistory).toHaveBeenCalled())
    await waitFor(() => {
      expect(screen.queryByTestId('timeline-event')).not.toBeInTheDocument()
    })
    expect(screen.queryByText('Coach chat')).not.toBeInTheDocument()
    expect(screen.queryByText('Open question')).not.toBeInTheDocument()
  })

  it('interleaves a plan update between conversation exchanges by time', async () => {
    setupStore({
      chatHistory: [
        { role: 'user', content: 'Older question', timestamp: '2026-06-15T10:00:00.000Z' },
        { role: 'assistant', content: 'Older answer', timestamp: '2026-06-15T10:00:01.000Z' },
        { role: 'user', content: 'Newer question', timestamp: '2026-06-15T10:05:00.000Z' },
        { role: 'assistant', content: 'Newer answer', timestamp: '2026-06-15T10:05:01.000Z' },
      ],
    })
    // recordedAt sits between the two exchanges.
    mockFetchPlanHistory.mockResolvedValue([planEntry({ recordedAt: '2026-06-15T10:02:00.000Z' })])
    render(<AIChat />)

    const planUpdate = await screen.findByText('Coach chat')
    const newerAnswer = screen.getByText('Newer answer')
    const olderQuestion = screen.getByText('Older question')

    // Newest first: newer exchange → plan update → older exchange.
    expect(newerAnswer.compareDocumentPosition(planUpdate) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(planUpdate.compareDocumentPosition(olderQuestion) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })
})

/**
 * Type into the pinned inquiry's answer box and wait for the state to land.
 *
 * `fireEvent.change` only queues the React state update. Submitting before it
 * flushes makes every submit path a no-op rather than merely late: `submit`
 * bails on an empty box, and the Send button carries `disabled={!answer.trim()}`.
 * The failure then reads as "the handler was never called", which looks like a
 * component bug instead of a test race — and waiting on the call count does not
 * help, because the call never happens (#534).
 */
async function typeAnswer(value: string) {
  // Re-fires the change until it sticks. PinnedInquiry clears its answer state
  // in an effect keyed on the question, so a change applied before that effect
  // runs is wiped and a single fireEvent silently loses the text.
  await waitFor(() => {
    const current = screen.getByLabelText(/^Answer:/)
    fireEvent.change(current, { target: { value } })
    expect(current).toHaveValue(value)
  })
  return screen.getByLabelText(/^Answer:/)
}

describe('AIChat pinned inquiries (#506)', () => {
  const inquiry = (overrides = {}) => ({
    id: 'inq-1',
    question: 'You skipped Tuesday three weeks running — what is getting in the way?',
    category: 'recurring_issues',
    whyAsking: 'Your rides show the absence but never the reason.',
    settingsHint: 'Settings > Athlete > Availability',
    status: 'pending' as const,
    answer: null,
    askCount: 1,
    followUpNote: null,
    askedAt: '2026-06-15T09:00:00.000Z',
    answeredAt: null,
    updatedAt: '2026-06-15T09:00:00.000Z',
    ...overrides,
  })

  it('pins the question outside the scrolling feed so it cannot be buried', async () => {
    setupStore()
    mockFetchAthleteInquiries.mockResolvedValue([inquiry()])
    render(<AIChat />)

    const pin = await screen.findByTestId('pinned-inquiry')
    expect(pin).toHaveTextContent('what is getting in the way?')
    expect(pin).toHaveTextContent('Your rides show the absence but never the reason.')
    expect(pin).toHaveTextContent('Settings > Athlete > Availability')
    // The pin must not live in the feed, which scrolls away under new exchanges.
    const feed = screen.getByLabelText('Coach chat messages')
    expect(feed.contains(pin)).toBe(false)
  })

  it('pins only the longest-waiting question, not every open one', async () => {
    setupStore()
    mockFetchAthleteInquiries.mockResolvedValue([
      inquiry(),
      inquiry({ id: 'inq-2', question: 'How is sleep at the moment?' }),
    ])
    render(<AIChat />)

    await screen.findByTestId('pinned-inquiry')
    expect(screen.getAllByTestId('pinned-inquiry')).toHaveLength(1)
    expect(screen.queryByText('How is sleep at the moment?')).not.toBeInTheDocument()
  })

  it('clears the pin on an accepted answer and shows the exchange in the chat', async () => {
    setupStore()
    mockFetchAthleteInquiries.mockResolvedValue([inquiry()])
    mockAnswerAthleteInquiry.mockResolvedValue({
      inquiry: inquiry({ status: 'answered', answer: 'Work trips.' }),
      accepted: true,
      coachReply: 'Thanks — I will move that session to Wednesday.',
    })
    render(<AIChat />)

    const pin = await screen.findByTestId('pinned-inquiry')
    await typeAnswer('Work trips.')
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))

    await waitFor(() => expect(screen.queryByTestId('pinned-inquiry')).not.toBeInTheDocument())
    expect(mockAnswerAthleteInquiry).toHaveBeenCalledWith('token-123', 'inq-1', 'Work trips.')
    expect(await screen.findByText('Thanks — I will move that session to Wednesday.')).toBeInTheDocument()
    expect(screen.getByText('Work trips.')).toBeInTheDocument()
    expect(pin).not.toBeInTheDocument()
  })

  it('keeps the pin with the rephrased question when the answer missed', async () => {
    setupStore()
    mockFetchAthleteInquiries.mockResolvedValue([inquiry()])
    const rephrased = inquiry({
      status: 'pending',
      askCount: 2,
      question: 'Is it a fixed commitment on Tuesdays, or just how it fell?',
    })
    mockAnswerAthleteInquiry.mockImplementation(async () => {
      // The answer re-pins a rephrased question server-side, so a list fetched
      // after this point sees the new wording too.
      mockFetchAthleteInquiries.mockResolvedValue([rephrased])
      return {
        inquiry: rephrased,
        accepted: false,
        coachReply: 'Fair enough — let me put it another way.',
      }
    })
    render(<AIChat />)

    await screen.findByTestId('pinned-inquiry')
    await typeAnswer('dunno')
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))

    const pin = await screen.findByTestId('pinned-inquiry')
    await waitFor(() =>
      expect(pin).toHaveTextContent('Is it a fixed commitment on Tuesdays, or just how it fell?'),
    )
    expect(pin).toHaveTextContent('Still needs an answer')
    // The answer that missed must not be left in the box for the new question.
    expect(screen.getByLabelText(/^Answer:/)).toHaveValue('')
  })

  it('drops the pin once the coach hands off to settings', async () => {
    setupStore()
    mockFetchAthleteInquiries.mockResolvedValue([inquiry({ askCount: 2 })])
    mockAnswerAthleteInquiry.mockResolvedValue({
      inquiry: inquiry({ askCount: 2, status: 'needs_settings' }),
      accepted: false,
      coachReply: 'No problem — you can set this any time under Settings > Athlete > Availability.',
    })
    render(<AIChat />)

    await screen.findByTestId('pinned-inquiry')
    await typeAnswer('still dunno')
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))

    await waitFor(() => expect(screen.queryByTestId('pinned-inquiry')).not.toBeInTheDocument())
    expect(
      await screen.findByText(/you can set this any time under Settings/),
    ).toBeInTheDocument()
  })

  it('skipping removes the pin without answering', async () => {
    setupStore()
    mockFetchAthleteInquiries.mockResolvedValue([inquiry()])
    render(<AIChat />)

    await screen.findByTestId('pinned-inquiry')
    fireEvent.click(screen.getByRole('button', { name: 'Skip for now' }))

    await waitFor(() => expect(screen.queryByTestId('pinned-inquiry')).not.toBeInTheDocument())
    expect(mockDismissAthleteInquiry).toHaveBeenCalledWith('token-123', 'inq-1')
    expect(mockAnswerAthleteInquiry).not.toHaveBeenCalled()
    // A list fetch that is still a moment behind must not re-pin it.
    await waitFor(() => expect(mockFetchAthleteInquiries).toHaveBeenCalled())
    expect(screen.queryByTestId('pinned-inquiry')).not.toBeInTheDocument()
  })

  it('keeps the pin and reports the failure when sending the answer fails', async () => {
    setupStore()
    mockFetchAthleteInquiries.mockResolvedValue([inquiry()])
    mockAnswerAthleteInquiry.mockRejectedValue(new Error('network'))
    render(<AIChat />)

    await screen.findByTestId('pinned-inquiry')
    await typeAnswer('Work trips.')
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))

    expect(await screen.findByText(/Couldn't send that answer/)).toBeInTheDocument()
    expect(screen.getByTestId('pinned-inquiry')).toBeInTheDocument()
    expect(screen.getByLabelText(/^Answer:/)).toHaveValue('Work trips.')
  })

  it('shows no pin when nothing is waiting', async () => {
    setupStore()
    render(<AIChat />)

    await waitFor(() => expect(mockFetchAthleteInquiries).toHaveBeenCalled())
    expect(screen.queryByTestId('pinned-inquiry')).not.toBeInTheDocument()
  })
})

describe('AIChat pinned inquiry edge cases (#506)', () => {
  const inquiry = (overrides = {}) => ({
    id: 'inq-1',
    question: 'You skipped Tuesday three weeks running — what is getting in the way?',
    category: 'recurring_issues',
    whyAsking: 'Your rides show the absence but never the reason.',
    settingsHint: 'Settings > Athlete > Availability',
    status: 'pending' as const,
    answer: null,
    askCount: 1,
    followUpNote: null,
    askedAt: '2026-06-15T09:00:00.000Z',
    answeredAt: null,
    updatedAt: '2026-06-15T09:00:00.000Z',
    ...overrides,
  })

  const answered = (coachReply: string) => ({
    inquiry: inquiry({ status: 'answered' as const, answer: 'Work trips.' }),
    accepted: true,
    coachReply,
  })

  it('sends the answer on Enter without needing the button', async () => {
    setupStore()
    mockFetchAthleteInquiries.mockResolvedValue([inquiry()])
    mockAnswerAthleteInquiry.mockResolvedValue(answered('Noted, thanks.'))
    render(<AIChat />)

    await screen.findByTestId('pinned-inquiry')
    const box = await typeAnswer('Work trips.')
    fireEvent.keyDown(box, { key: 'Enter' })

    await waitFor(() =>
      expect(mockAnswerAthleteInquiry).toHaveBeenCalledWith('token-123', 'inq-1', 'Work trips.'),
    )
  })

  it('leaves the answer alone on shift+Enter so it can be a new line', async () => {
    setupStore()
    mockFetchAthleteInquiries.mockResolvedValue([inquiry()])
    render(<AIChat />)

    await screen.findByTestId('pinned-inquiry')
    const box = await typeAnswer('Work trips,')
    fireEvent.keyDown(box, { key: 'Enter', shiftKey: true })

    expect(mockAnswerAthleteInquiry).not.toHaveBeenCalled()
    expect(box).toHaveValue('Work trips,')
  })

  it('ignores Enter on an empty answer box', async () => {
    setupStore()
    mockFetchAthleteInquiries.mockResolvedValue([inquiry()])
    render(<AIChat />)

    await screen.findByTestId('pinned-inquiry')
    fireEvent.keyDown(screen.getByLabelText(/^Answer:/), { key: 'Enter' })
    fireEvent.keyDown(screen.getByLabelText(/^Answer:/), { key: 'Enter' })

    expect(mockAnswerAthleteInquiry).not.toHaveBeenCalled()
    expect(screen.getByTestId('pinned-inquiry')).toBeInTheDocument()
  })

  it('sends only once while an answer is already in flight', async () => {
    setupStore()
    mockFetchAthleteInquiries.mockResolvedValue([inquiry()])
    let release: (value: unknown) => void = () => {}
    mockAnswerAthleteInquiry.mockImplementation(
      () => new Promise((resolve) => { release = resolve }),
    )
    render(<AIChat />)

    await screen.findByTestId('pinned-inquiry')
    const box = await typeAnswer('Work trips.')
    fireEvent.keyDown(box, { key: 'Enter' })
    fireEvent.keyDown(box, { key: 'Enter' })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))

    // waitFor still catches a genuine double send: at two calls this can never
    // settle on one, so the test times out rather than passing.
    await waitFor(() => expect(mockAnswerAthleteInquiry).toHaveBeenCalledTimes(1))
    release(answered('Noted.'))
    await waitFor(() => expect(screen.queryByTestId('pinned-inquiry')).not.toBeInTheDocument())
  })

  it('adds no coach message when the reply comes back empty', async () => {
    setupStore()
    mockFetchAthleteInquiries.mockResolvedValue([inquiry()])
    mockAnswerAthleteInquiry.mockResolvedValue(answered(''))
    render(<AIChat />)

    await screen.findByTestId('pinned-inquiry')
    await typeAnswer('Work trips.')
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))

    await waitFor(() => expect(screen.queryByTestId('pinned-inquiry')).not.toBeInTheDocument())
    // Question and answer are still recorded; there is simply no third message.
    const chat = useAppStore.getState().chatHistory
    expect(chat.map((m) => m.content)).toEqual([
      'You skipped Tuesday three weeks running — what is getting in the way?',
      'Work trips.',
    ])
  })

  it('keeps the pin and reports the failure when skipping fails', async () => {
    setupStore()
    mockFetchAthleteInquiries.mockResolvedValue([inquiry()])
    mockDismissAthleteInquiry.mockRejectedValue(new Error('network'))
    render(<AIChat />)

    await screen.findByTestId('pinned-inquiry')
    fireEvent.click(screen.getByRole('button', { name: 'Skip for now' }))

    expect(await screen.findByText(/Couldn't skip that/)).toBeInTheDocument()
    expect(screen.getByTestId('pinned-inquiry')).toBeInTheDocument()
  })

  it('does nothing on answer or skip once the athlete is signed out', async () => {
    setupStore()
    mockFetchAthleteInquiries.mockResolvedValue([inquiry()])
    render(<AIChat />)

    await screen.findByTestId('pinned-inquiry')
    // Session ends (expiry or sign-out) while the question is still pinned.
    useAppStore.setState({ authToken: null })

    await typeAnswer('Work trips.')
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    // Wait for the send to settle before skipping: both buttons disable while one
    // is in flight, so a same-tick second click would never reach the handler.
    const skip = screen.getByRole('button', { name: 'Skip for now' })
    await waitFor(() => expect(skip).toBeEnabled())
    fireEvent.click(skip)

    await waitFor(() => expect(screen.getByTestId('pinned-inquiry')).toBeInTheDocument())
    expect(mockAnswerAthleteInquiry).not.toHaveBeenCalled()
    expect(mockDismissAthleteInquiry).not.toHaveBeenCalled()
  })
})

describe('AIChat pinned inquiry resilience (#506)', () => {
  const inquiry = (overrides = {}) => ({
    id: 'inq-1',
    question: 'You skipped Tuesday three weeks running — what is getting in the way?',
    category: 'recurring_issues',
    whyAsking: 'Your rides show the absence but never the reason.',
    settingsHint: 'Settings > Athlete > Availability',
    status: 'pending' as const,
    answer: null,
    askCount: 1,
    followUpNote: null,
    askedAt: '2026-06-15T09:00:00.000Z',
    answeredAt: null,
    updatedAt: '2026-06-15T09:00:00.000Z',
    ...overrides,
  })

  it('degrades to no pin when the inquiry fetch fails', async () => {
    setupStore()
    mockFetchAthleteInquiries.mockRejectedValue(new Error('offline'))
    render(<AIChat />)

    // The rest of the timeline still renders; a failed fetch must not break the chat.
    await waitFor(() => expect(mockFetchAthleteInquiries).toHaveBeenCalled())
    expect(screen.queryByTestId('pinned-inquiry')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Coach chat messages')).toBeInTheDocument()
  })

  it('locks both controls while a skip is in flight so it cannot fire twice', async () => {
    setupStore()
    mockFetchAthleteInquiries.mockResolvedValue([inquiry()])
    let release: (value: unknown) => void = () => {}
    mockDismissAthleteInquiry.mockImplementation(
      () => new Promise((resolve) => { release = resolve }),
    )
    render(<AIChat />)

    await screen.findByTestId('pinned-inquiry')
    const skip = screen.getByRole('button', { name: 'Skip for now' })
    fireEvent.click(skip)

    // The disabled attributes are the single guard against a double submit.
    await waitFor(() => expect(skip).toBeDisabled())
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
    expect(screen.getByLabelText(/^Answer:/)).toBeDisabled()
    fireEvent.click(skip)
    expect(mockDismissAthleteInquiry).toHaveBeenCalledTimes(1)

    release(undefined)
    await waitFor(() => expect(screen.queryByTestId('pinned-inquiry')).not.toBeInTheDocument())
  })

  it('keeps the rephrased wording when a stale list fetch lands after the answer', async () => {
    // Answering appends chat messages, which re-runs the list effect. Hold that
    // refetch open so it resolves *after* the rephrase is applied, carrying the
    // pre-rephrase wording — the ordering that made this flaky in CI.
    setupStore()
    let release: (value: unknown) => void = () => {}
    mockFetchAthleteInquiries
      .mockResolvedValueOnce([inquiry()])
      .mockImplementation(() => new Promise((resolve) => { release = resolve }))
    mockAnswerAthleteInquiry.mockResolvedValue({
      inquiry: inquiry({ askCount: 2, question: 'Is it a fixed commitment on Tuesdays?' }),
      accepted: false,
      coachReply: 'Let me put it another way.',
    })
    render(<AIChat />)

    await screen.findByTestId('pinned-inquiry')
    await typeAnswer('dunno')
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))

    await waitFor(() =>
      expect(screen.getByTestId('pinned-inquiry')).toHaveTextContent(
        'Is it a fixed commitment on Tuesdays?',
      ),
    )

    // The stale list (askCount 1) must not roll the pin back to the old wording.
    release([inquiry()])
    await waitFor(() => expect(mockFetchAthleteInquiries).toHaveBeenCalledTimes(2))
    expect(screen.getByTestId('pinned-inquiry')).toHaveTextContent(
      'Is it a fixed commitment on Tuesdays?',
    )
  })

  it('leaves the other pending question untouched when one is rephrased', async () => {
    setupStore()
    const second = inquiry({ id: 'inq-2', question: 'How is sleep at the moment?' })
    mockFetchAthleteInquiries.mockResolvedValue([inquiry(), second])
    mockAnswerAthleteInquiry.mockResolvedValue({
      inquiry: inquiry({ askCount: 2, question: 'Is it a fixed commitment on Tuesdays?' }),
      accepted: false,
      coachReply: 'Let me put it another way.',
    })
    render(<AIChat />)

    await screen.findByTestId('pinned-inquiry')
    await typeAnswer('dunno')
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))

    await waitFor(() =>
      expect(screen.getByTestId('pinned-inquiry')).toHaveTextContent(
        'Is it a fixed commitment on Tuesdays?',
      ),
    )
    // The rephrase must not disturb the queue behind it.
    mockFetchAthleteInquiries.mockResolvedValue([second])
    fireEvent.click(screen.getByRole('button', { name: 'Skip for now' }))
    await waitFor(() =>
      expect(screen.getByTestId('pinned-inquiry')).toHaveTextContent('How is sleep at the moment?'),
    )
  })

  it('does not re-pin an answered question when a slower fetch lands after it', async () => {
    setupStore()
    let release: (value: unknown) => void = () => {}
    mockFetchAthleteInquiries
      .mockResolvedValueOnce([inquiry()])
      // The refetch triggered by the answer starts before the answer resolves and
      // still carries the pre-answer list.
      .mockImplementation(() => new Promise((resolve) => { release = resolve }))
    mockAnswerAthleteInquiry.mockResolvedValue({
      inquiry: inquiry({ status: 'answered' as const, answer: 'Work trips.' }),
      accepted: true,
      coachReply: 'Noted.',
    })
    render(<AIChat />)

    await screen.findByTestId('pinned-inquiry')
    await typeAnswer('Work trips.')
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    await waitFor(() => expect(screen.queryByTestId('pinned-inquiry')).not.toBeInTheDocument())

    release([inquiry()])
    await waitFor(() => expect(mockFetchAthleteInquiries).toHaveBeenCalledTimes(2))
    expect(screen.queryByTestId('pinned-inquiry')).not.toBeInTheDocument()
  })
})

describe('AIChat pinned inquiry vs. background refresh (#506)', () => {
  const inquiry = {
    id: 'inq-1',
    question: 'You skipped Tuesday three weeks running — what is getting in the way?',
    category: 'recurring_issues',
    whyAsking: 'Your rides show the absence but never the reason.',
    settingsHint: 'Settings > Athlete > Availability',
    status: 'pending' as const,
    answer: null,
    askCount: 1,
    followUpNote: null,
    askedAt: '2026-06-15T09:00:00.000Z',
    answeredAt: null,
    updatedAt: '2026-06-15T09:00:00.000Z',
  }

  it('discards a list fetch that was already in flight when the athlete skipped', async () => {
    setupStore()
    let releaseFetch: (value: unknown) => void = () => {}
    mockFetchAthleteInquiries
      .mockResolvedValueOnce([inquiry])
      .mockImplementation(() => new Promise((resolve) => { releaseFetch = resolve }))
    mockAskTrainer.mockResolvedValue({ response: 'Sure thing.' })
    mockAnswerAthleteInquiry.mockResolvedValue({
      inquiry: { ...inquiry, status: 'answered' as const, answer: 'Work trips.' },
      accepted: true,
      coachReply: 'Noted.',
    })
    render(<AIChat />)

    await screen.findByTestId('pinned-inquiry')

    // An ordinary chat message kicks off a refresh that is still in flight...
    fireEvent.change(screen.getByLabelText('Message to coach'), {
      target: { value: 'How is my week looking?' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }))
    await waitFor(() => expect(mockFetchAthleteInquiries).toHaveBeenCalledTimes(2))

    // ...while the athlete skips the pinned question. A skip writes no chat
    // message, so the effect is never re-run and that fetch is never cancelled —
    // the handled-id filter is the only thing standing between it and a re-pin.
    fireEvent.click(screen.getByRole('button', { name: 'Skip for now' }))
    await waitFor(() => expect(screen.queryByTestId('pinned-inquiry')).not.toBeInTheDocument())

    // The stale list resolves last and must not resurrect the skipped question.
    releaseFetch([inquiry])
    await Promise.resolve()
    await waitFor(() => expect(screen.queryByTestId('pinned-inquiry')).not.toBeInTheDocument())
  })
})
