import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import RideFeedbackForm from './RideFeedbackForm'

// Mock the submitRideFeedback service so no real HTTP calls are made.
const mockSubmit = vi.hoisted(() => vi.fn())
const mockSaveChatMessage = vi.hoisted(() => vi.fn())
const mockAddPendingFeedbackRide = vi.hoisted(() => vi.fn())
const mockAddChatMessage = vi.hoisted(() => vi.fn())
vi.mock('../services/user', () => ({
  submitRideFeedback: mockSubmit,
  saveChatMessage: mockSaveChatMessage,
}))

// Provide a minimal auth token + updateTrainingDay via the store mock.
vi.mock('../store/useAppStore', () => ({
  useAppStore: () => ({
    authToken: 'test-token',
    addPendingFeedbackRide: mockAddPendingFeedbackRide,
    addChatMessage: mockAddChatMessage,
  }),
}))

function renderForm(props?: Partial<Parameters<typeof RideFeedbackForm>[0]>) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const defaultProps = {
    stravaActivityId: 1234,
    activityDate: '2026-04-20',
    activityName: 'Morning Endurance',
    onSaved: vi.fn(),
    onCancel: vi.fn(),
    ...props,
  }
  return {
    ...render(
      <QueryClientProvider client={qc}>
        <RideFeedbackForm {...defaultProps} />
      </QueryClientProvider>,
    ),
    props: defaultProps,
  }
}

describe('RideFeedbackForm', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders the form title and activity date', () => {
    renderForm()
    expect(screen.getByText('How was your ride?')).toBeInTheDocument()
    expect(screen.getByText('2026-04-20')).toBeInTheDocument()
  })

  it('shows RPE slider and range labels', () => {
    renderForm()
    expect(screen.getByRole('slider')).toBeInTheDocument()
    expect(screen.getByText('Very easy')).toBeInTheDocument()
    expect(screen.getByText('Max effort')).toBeInTheDocument()
  })

  it('shows all three legs-feeling options', () => {
    renderForm()
    expect(screen.getByRole('button', { name: /Fresh/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Normal/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Heavy/i })).toBeInTheDocument()
  })

  it('shows all five intent options', () => {
    renderForm()
    expect(screen.getByRole('button', { name: /Planned workout/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Recovery/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Commute/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Free ride/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Aborted/i })).toBeInTheDocument()
  })

  it('uses the activity type in copy and non-cycling intent labels', () => {
    renderForm({ sportType: 'Hike', activityName: 'Hill Loop' })

    expect(screen.getByText('How was your hike?')).toBeInTheDocument()
    expect(screen.getByText('2026-04-20 · Hike')).toBeInTheDocument()
    expect(screen.getByText('What was this hike?')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Free activity/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Free ride/i })).not.toBeInTheDocument()
  })

  it('shows optional note textarea and Save/Cancel buttons', () => {
    renderForm()
    expect(screen.getByPlaceholderText(/Anything else the coach should know/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /save feedback/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /cancel/i })).toBeInTheDocument()
  })

  it('calls onCancel when the Cancel button is clicked', async () => {
    const { props } = renderForm()
    await userEvent.click(screen.getByRole('button', { name: /cancel/i }))
    expect(props.onCancel).toHaveBeenCalledTimes(1)
  })

  it('calls submitRideFeedback with the form data and invokes onSaved on success', async () => {
    const userNote = 'RPE 7/10 | legs: heavy | intent: recovery'
    mockSubmit.mockResolvedValue({
      stravaActivityId: 1234,
      userNote,
      coachNote: 'Take it easy tomorrow.',
    })

    const { props } = renderForm()

    // Select "Heavy" legs
    await userEvent.click(screen.getByRole('button', { name: /Heavy/i }))

    // Select "Recovery" intent
    await userEvent.click(screen.getByRole('button', { name: /Recovery/i }))

    // Submit the form
    await userEvent.click(screen.getByRole('button', { name: /save feedback/i }))

    expect(mockSubmit).toHaveBeenCalledWith(
      'test-token',
      1234,
      expect.objectContaining({
        legs: 'heavy',
        intent: 'recovery',
      }),
    )

    expect(mockAddPendingFeedbackRide).toHaveBeenCalledWith(1234)
    expect(mockAddChatMessage).toHaveBeenNthCalledWith(
      1,
      expect.objectContaining({
        role: 'user',
        content: `Ride feedback for your ride "Morning Endurance" on 2026-04-20: ${userNote}`,
      }),
    )
    expect(mockAddChatMessage).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({
        role: 'assistant',
        content: 'About your ride "Morning Endurance" on 2026-04-20: Take it easy tomorrow.',
      }),
    )
    expect(mockSaveChatMessage).toHaveBeenNthCalledWith(
      1,
      'test-token',
      expect.objectContaining({
        role: 'user',
        content: `Ride feedback for your ride "Morning Endurance" on 2026-04-20: ${userNote}`,
      }),
    )
    expect(mockSaveChatMessage).toHaveBeenNthCalledWith(
      2,
      'test-token',
      expect.objectContaining({
        role: 'assistant',
        content: 'About your ride "Morning Endurance" on 2026-04-20: Take it easy tomorrow.',
      }),
    )

    // After feedback is saved the recommendation step is shown; dismiss it
    const gotItButton = await screen.findByRole('button', { name: /got it/i })
    await userEvent.click(gotItButton)

    expect(props.onSaved).toHaveBeenCalledWith(
      expect.objectContaining({ userNote, coachNote: 'Take it easy tomorrow.' }),
    )
  })

  it('includes optional note when filled', async () => {
    mockSubmit.mockResolvedValue({
      stravaActivityId: 1234,
      userNote: 'RPE 5/10 | legs: normal | intent: free ride | Great day',
    })

    renderForm()

    await userEvent.type(
      screen.getByPlaceholderText(/Anything else the coach should know/i),
      'Great day',
    )

    await userEvent.click(screen.getByRole('button', { name: /save feedback/i }))

    expect(mockSubmit).toHaveBeenCalledWith(
      'test-token',
      1234,
      expect.objectContaining({ note: 'Great day' }),
    )
  })
})
