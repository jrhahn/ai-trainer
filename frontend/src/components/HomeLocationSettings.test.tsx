import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import HomeLocationSettings from './HomeLocationSettings'
import { useAppStore } from '../store/useAppStore'

const { mockFetchHomeLocation, mockSaveHomeLocation } = vi.hoisted(() => ({
  mockFetchHomeLocation: vi.fn(),
  mockSaveHomeLocation: vi.fn(),
}))

vi.mock('../services/user', () => ({
  fetchHomeLocation: mockFetchHomeLocation,
  saveHomeLocation: mockSaveHomeLocation,
}))

function renderCard() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <HomeLocationSettings />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  useAppStore.getState().resetAll()
  useAppStore.setState({ authToken: 'tok-123' })
  mockFetchHomeLocation.mockReset()
  mockSaveHomeLocation.mockReset()
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

  it('saves an override and reflects it in the store', async () => {
    mockFetchHomeLocation.mockResolvedValue(null)
    const saved = {
      latitude: 52.52,
      longitude: 13.4,
      label: 'Berlin',
      source: 'user_set',
      confidence: 1,
      rideCount: 0,
    }
    mockSaveHomeLocation.mockResolvedValue(saved)

    renderCard()
    const user = userEvent.setup()

    await user.type(await screen.findByLabelText(/Place name/), 'Berlin')
    await user.type(screen.getByLabelText(/Latitude/), '52.52')
    await user.type(screen.getByLabelText(/Longitude/), '13.4')
    await user.click(screen.getByRole('button', { name: /Save location/ }))

    await waitFor(() =>
      expect(mockSaveHomeLocation).toHaveBeenCalledWith('tok-123', {
        latitude: 52.52,
        longitude: 13.4,
        label: 'Berlin',
      }),
    )
    await waitFor(() =>
      expect(useAppStore.getState().homeLocation).toEqual(saved),
    )
  })

  it('refuses to save coordinates that are not on the globe', async () => {
    mockFetchHomeLocation.mockResolvedValue(null)

    renderCard()
    const user = userEvent.setup()

    await user.type(await screen.findByLabelText(/Latitude/), '120')
    await user.type(screen.getByLabelText(/Longitude/), '13.4')

    expect(screen.getByRole('button', { name: /Save location/ })).toBeDisabled()
    expect(mockSaveHomeLocation).not.toHaveBeenCalled()
  })

  it('surfaces a save failure instead of pretending it worked', async () => {
    mockFetchHomeLocation.mockResolvedValue(null)
    mockSaveHomeLocation.mockRejectedValue(new Error('Backend exploded'))

    renderCard()
    const user = userEvent.setup()

    await user.type(await screen.findByLabelText(/Latitude/), '52.52')
    await user.type(screen.getByLabelText(/Longitude/), '13.4')
    await user.click(screen.getByRole('button', { name: /Save location/ }))

    expect(await screen.findByText('Backend exploded')).toBeInTheDocument()
  })
})
