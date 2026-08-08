import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import SessionPurposeQuestion from './SessionPurposeQuestion'
import { useAppStore } from '../store/useAppStore'
import type { RideMetricPoint } from '../store/useAppStore'

const mockAnswer = vi.hoisted(() => vi.fn())
vi.mock('../services/user', () => ({ answerRidePurpose: mockAnswer }))

const DATE = '2026-05-06'

function makeRide(overrides: Partial<RideMetricPoint> = {}): RideMetricPoint {
  return {
    stravaActivityId: 7101,
    activityDate: DATE,
    activityName: 'Evening ride',
    sportType: 'Ride',
    ridePurpose: 'unknown',
    classificationConfidence: 'low',
    purposeQuestionOpen: true,
    ...overrides,
  }
}

function renderQuestion(ride: RideMetricPoint) {
  // No QueryClientProvider on purpose: this sits in activity rows on pages that
  // do not otherwise need one, so it must not require ambient context.
  return render(<SessionPurposeQuestion ride={ride} />)
}

beforeEach(() => {
  vi.clearAllMocks()
  useAppStore.getState().resetAll()
  useAppStore.setState({ authToken: 'test-token' })
  mockAnswer.mockResolvedValue({
    stravaActivityId: 7101,
    ride: makeRide({
      ridePurpose: 'interval_threshold',
      classificationConfidence: 'athlete',
      purposeQuestionOpen: false,
      purposeQuestionStatus: 'answered',
    }),
  })
})

describe('SessionPurposeQuestion', () => {
  it('asks what the session was when the classifier could not tell', () => {
    renderQuestion(makeRide())
    expect(screen.getByText('What was this session?')).toBeInTheDocument()
    expect(screen.getByText('Threshold intervals')).toBeInTheDocument()
  })

  it('says why it is asking rather than just demanding an answer', () => {
    renderQuestion(makeRide())
    expect(screen.getByText(/asking rather than guessing/i)).toBeInTheDocument()
  })

  it('costs a cleanly classified ride nothing', () => {
    renderQuestion(makeRide({ purposeQuestionOpen: false, ridePurpose: 'endurance' }))
    expect(screen.queryByTestId('session-purpose-question')).not.toBeInTheDocument()
  })

  it('stays silent for a signed-out store rather than offering a dead button', () => {
    useAppStore.setState({ authToken: null })
    renderQuestion(makeRide())
    expect(screen.queryByTestId('session-purpose-question')).not.toBeInTheDocument()
  })

  it("sends the athlete's answer for this ride", async () => {
    const user = userEvent.setup()
    renderQuestion(makeRide({ externalActivityId: 'i1234' }))

    await user.click(screen.getByText('VO2max intervals'))

    await waitFor(() =>
      expect(mockAnswer).toHaveBeenCalledWith('test-token', 7101, 'interval_vo2max', 'i1234')
    )
  })

  it('confirms the answer landed instead of just vanishing', async () => {
    const user = userEvent.setup()
    renderQuestion(makeRide())

    await user.click(screen.getByText('Threshold intervals'))

    expect(await screen.findByTestId('session-purpose-answered')).toHaveTextContent(
      /Threshold intervals/
    )
  })

  it('writes the updated ride back to the store', async () => {
    const user = userEvent.setup()
    useAppStore.setState({ rideMetricsHistory: [makeRide()] })
    renderQuestion(makeRide())

    await user.click(screen.getByText('Endurance'))

    await waitFor(() =>
      expect(useAppStore.getState().rideMetricsHistory[0].ridePurpose).toBe(
        'interval_threshold'
      )
    )
  })

  it('lets the athlete decline, and sends that as a skip', async () => {
    const user = userEvent.setup()
    mockAnswer.mockResolvedValue({
      stravaActivityId: 7101,
      ride: makeRide({ purposeQuestionOpen: false, purposeQuestionStatus: 'skipped' }),
    })
    renderQuestion(makeRide())

    await user.click(screen.getByText(/rather not say/i))

    await waitFor(() =>
      expect(mockAnswer).toHaveBeenCalledWith('test-token', 7101, null, undefined)
    )
    // A skip asserts nothing about the session, so there is nothing to confirm.
    expect(screen.queryByTestId('session-purpose-answered')).not.toBeInTheDocument()
  })

  it('names the ride in each choice, since several can be on screen at once', () => {
    renderQuestion(makeRide({ activityName: 'Evening ride' }))
    expect(
      screen.getByLabelText('“Evening ride” was Threshold intervals')
    ).toBeInTheDocument()
    expect(screen.getByLabelText('Skip the question about “Evening ride”')).toBeInTheDocument()
  })

  it('keeps the question open when saving fails', async () => {
    const user = userEvent.setup()
    mockAnswer.mockRejectedValue(new Error('nope'))
    renderQuestion(makeRide())

    await user.click(screen.getByText('Recovery'))

    expect(await screen.findByText(/Could not save that/i)).toBeInTheDocument()
    expect(screen.getByTestId('session-purpose-question')).toBeInTheDocument()
  })
})
