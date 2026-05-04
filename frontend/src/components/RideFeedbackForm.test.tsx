import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import RideFeedbackForm from './RideFeedbackForm'

// Mock the submitRideFeedback service so no real HTTP calls are made.
const mockSubmit = vi.hoisted(() => vi.fn())
vi.mock('../services/user', () => ({
  submitRideFeedback: mockSubmit,
}))

// Mock fetchNextRideRecommendation so the recommendation step resolves immediately.
const mockRecommend = vi.hoisted(() => vi.fn())
vi.mock('../services/ai', () => ({
  fetchNextRideRecommendation: mockRecommend,
}))

// Provide a minimal auth token + updateTrainingDay via the store mock.
vi.mock('../store/useAppStore', () => ({
  useAppStore: () => ({ authToken: 'test-token', updateTrainingDay: vi.fn() }),
}))

function renderForm(props?: Partial<Parameters<typeof RideFeedbackForm>[0]>) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const defaultProps = {
    stravaActivityId: 1234,
    activityDate: '2026-04-20',
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
    mockSubmit.mockResolvedValue({ stravaActivityId: 1234, userNote })
    mockRecommend.mockResolvedValue({
      response: 'Take it easy tomorrow.',
      nextSessionRecommendation: 'Rest day recommended.',
      recommendationType: 'recovery',
      planUpdates: undefined,
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

    // After feedback is saved the recommendation step is shown; dismiss it
    const gotItButton = await screen.findByRole('button', { name: /got it/i })
    await userEvent.click(gotItButton)

    expect(props.onSaved).toHaveBeenCalledWith(userNote)
  })

  it('includes optional note when filled', async () => {
    mockSubmit.mockResolvedValue({
      stravaActivityId: 1234,
      userNote: 'RPE 5/10 | legs: normal | intent: free ride | Great day',
    })
    mockRecommend.mockResolvedValue({
      response: 'Good effort.',
      nextSessionRecommendation: 'Keep the plan.',
      recommendationType: 'keep_as_planned',
      planUpdates: undefined,
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

