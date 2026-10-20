import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import CoachReviewConsentCard from './CoachReviewConsentCard'

const mockFetch = vi.hoisted(() => vi.fn())
const mockUpdate = vi.hoisted(() => vi.fn())
vi.mock('../services/user', () => ({
  fetchCoachReviewConsent: mockFetch,
  updateCoachReviewConsent: mockUpdate,
}))

const mockUiLanguage = vi.hoisted(() => ({ value: 'auto' }))
vi.mock('../store/useAppStore', () => ({
  useAppStore: (selector: (state: { authToken: string; uiLanguage: string }) => unknown) =>
    selector({ authToken: 'test-token', uiLanguage: mockUiLanguage.value }),
}))

const OFF = { available: false, consented: false, consentedAt: null, version: '2026-10-20' }
const OPEN = { ...OFF, available: true }
const GIVEN = { ...OPEN, consented: true, consentedAt: '2026-10-21T08:00:00Z' }

function renderCard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <CoachReviewConsentCard />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('CoachReviewConsentCard (ai-trainer-ops#136)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockUiLanguage.value = 'auto'
  })

  it('shows nothing while the opt-in is switched off', async () => {
    mockFetch.mockResolvedValue(OFF)
    const { container } = renderCard()

    await waitFor(() => expect(mockFetch).toHaveBeenCalledWith('test-token'))
    expect(container).toBeEmptyDOMElement()
  })

  it('is never ticked in advance, and ticking it gives the consent', async () => {
    mockFetch.mockResolvedValue(OPEN)
    mockUpdate.mockResolvedValue(GIVEN)
    renderCard()

    const box = await screen.findByRole('checkbox', { name: /go to Anthropic \(USA\).*health data/ })
    expect(box).not.toBeChecked()
    expect(screen.getByRole('link', { name: 'What happens with it' })).toHaveAttribute('href', '/privacy#coach-review')

    await userEvent.click(box)

    expect(mockUpdate).toHaveBeenCalledWith('test-token', true)
    expect(await screen.findByText(/version 2026-10-20\)\. Untick to withdraw\./)).toBeInTheDocument()
    expect(screen.getByRole('checkbox')).toBeChecked()
  })

  it('unticking withdraws at once', async () => {
    mockFetch.mockResolvedValue(GIVEN)
    mockUpdate.mockResolvedValue(OPEN)
    renderCard()

    await userEvent.click(await screen.findByRole('checkbox'))

    expect(mockUpdate).toHaveBeenCalledWith('test-token', false)
    await waitFor(() => expect(screen.getByRole('checkbox')).not.toBeChecked())
    expect(screen.queryByText(/Untick to withdraw/)).not.toBeInTheDocument()
  })

  it('says so when the choice was not saved', async () => {
    mockFetch.mockResolvedValue(OPEN)
    mockUpdate.mockRejectedValue(new Error('boom'))
    renderCard()

    await userEvent.click(await screen.findByRole('checkbox'))

    expect(await screen.findByText('Could not save your choice. Please try again.')).toBeInTheDocument()
    expect(screen.getByRole('checkbox')).not.toBeChecked()
  })

  it('is German in a German interface', async () => {
    mockUiLanguage.value = 'de'
    mockFetch.mockResolvedValue(GIVEN)
    renderCard()

    expect(await screen.findByRole('checkbox', { name: /Ich willige ein.*Gesundheitsangaben\./ })).toBeChecked()
    expect(screen.getByText(/^Eingewilligt am .* \(Fassung 2026-10-20\)\./)).toBeInTheDocument()
  })
})
