import { useState } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import HomeLocationSettings, { type HomeLocationDraft } from './HomeLocationSettings'
import { useAppStore } from '../store/useAppStore'

const { mockFetchHomeLocation } = vi.hoisted(() => ({
  mockFetchHomeLocation: vi.fn(),
}))

vi.mock('../services/user', () => ({
  fetchHomeLocation: mockFetchHomeLocation,
}))

// The Profile section owns the draft and saves it (ai-trainer-ops#93); this
// stands in for it and records every draft the fields hand up.
const drafts: HomeLocationDraft[] = []

function Owner() {
  const [draft, setDraft] = useState<HomeLocationDraft | null>(null)
  return (
    <HomeLocationSettings
      draft={draft}
      onDraft={(next) => {
        drafts.push(next)
        setDraft(next)
      }}
    />
  )
}

function renderCard() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <Owner />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  useAppStore.getState().resetAll()
  useAppStore.setState({ authToken: 'tok-123' })
  mockFetchHomeLocation.mockReset()
  drafts.length = 0
})

describe('HomeLocationSettings', () => {
  it('explains the latest-ride fallback when nothing is stored', async () => {
    mockFetchHomeLocation.mockResolvedValue(null)

    renderCard()

    expect(await screen.findByText(/Not set yet/)).toBeInTheDocument()
  })

  it('shows how much history backs an inferred location', async () => {
    mockFetchHomeLocation.mockResolvedValue({
      latitude: 47.99,
      longitude: 7.85,
      label: '',
      source: 'inferred',
      confidence: 0.62,
      rideCount: 34,
    })

    renderCard()

    expect(
      await screen.findByText(/Inferred from 34 clustered ride starts/),
    ).toBeInTheDocument()
    expect(await screen.findByText(/confidence 0\.62/)).toBeInTheDocument()
  })

  it('reads a ride count of zero when the backend omits it', async () => {
    mockFetchHomeLocation.mockResolvedValue({
      latitude: 47.99,
      longitude: 7.85,
      label: '',
      source: 'inferred',
      confidence: 0.4,
    })

    renderCard()

    expect(
      await screen.findByText(/Inferred from 0 clustered ride starts/),
    ).toBeInTheDocument()
  })

  it('states that an athlete-set location will not be overwritten', async () => {
    mockFetchHomeLocation.mockResolvedValue({
      latitude: 47.99,
      longitude: 7.85,
      label: 'Freiburg',
      source: 'user_set',
      confidence: 1,
      rideCount: 0,
    })

    renderCard()

    expect(
      await screen.findByText(/Automatic inference will not overwrite it/),
    ).toBeInTheDocument()
  })

  it('seeds the form from the stored location so the athlete corrects a real value', async () => {
    mockFetchHomeLocation.mockResolvedValue({
      latitude: 47.99,
      longitude: 7.85,
      label: 'Freiburg',
      source: 'inferred',
      confidence: 0.5,
      rideCount: 9,
    })

    renderCard()

    expect(await screen.findByDisplayValue('Freiburg')).toBeInTheDocument()
    expect(screen.getByDisplayValue('47.99')).toBeInTheDocument()
    expect(screen.getByDisplayValue('7.85')).toBeInTheDocument()
  })

  it('hands every edit up as the whole location, stored values included', async () => {
    mockFetchHomeLocation.mockResolvedValue({
      latitude: 47.99,
      longitude: 7.85,
      label: 'Freiburg',
      source: 'inferred',
      confidence: 0.5,
      rideCount: 9,
    })

    renderCard()
    const user = userEvent.setup()
    const place = await screen.findByLabelText(/Place name/)
    await user.clear(place)
    await user.type(place, 'Basel')

    expect(drafts.at(-1)).toEqual({ label: 'Basel', latitude: '47.99', longitude: '7.85' })
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('offers to make a latest-ride fallback permanent', async () => {
    mockFetchHomeLocation.mockResolvedValue({
      latitude: 52.52,
      longitude: 13.4,
      label: '',
      source: 'latest_ride',
      confidence: 0,
      rideCount: 0,
    })

    renderCard()

    expect(
      await screen.findByText(/Taken from your most recent ride with GPS/),
    ).toBeInTheDocument()
  })
})
