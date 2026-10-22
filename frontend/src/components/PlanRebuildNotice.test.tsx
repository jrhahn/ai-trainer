import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import PlanRebuildNotice, { NOTICE_MAX_AGE_MS, REASON_TEXT, seenKey } from './PlanRebuildNotice'
import { useAppStore } from '../store/useAppStore'
import type { UserProfile } from '../store/useAppStore'

const { mockChanged } = vi.hoisted(() => ({ mockChanged: vi.fn() }))
vi.mock('../services/ai', () => ({ fetchPlanInputsChanged: mockChanged }))

const EMAIL = 'mara@example.com'

function signIn() {
  useAppStore.setState({ authToken: 'tok', userProfile: { name: 'Mara', email: EMAIL } as UserProfile })
}

beforeEach(() => {
  useAppStore.getState().resetAll()
  vi.clearAllMocks()
  localStorage.clear()
  mockChanged.mockResolvedValue({ changed: false, reasons: [], rebuildsLeft: 6, lastRebuild: null })
})

afterEach(() => {
  useAppStore.setState({ uiLanguage: 'en' })
})

describe('PlanRebuildNotice (ai-trainer-ops#206)', () => {
  it('says in one sentence why the app just rebuilt the plan', () => {
    signIn()
    useAppStore.setState({ planRebuild: { at: new Date().toISOString(), reasons: ['training_days', 'race'] } })
    render(<PlanRebuildNotice />)

    expect(
      screen.getByText(/^Your plan is rebuilt: your training days changed, your race changed\. Done and logged sessions stay as they are\./),
    ).toBeInTheDocument()
    // No button to rebuild: the app did it.
    expect(screen.queryByRole('button', { name: /rebuild/i })).not.toBeInTheDocument()
  })

  it('shows the last rebuild the server recorded, such as the nightly one', async () => {
    signIn()
    mockChanged.mockResolvedValue({
      changed: false,
      reasons: [],
      rebuildsLeft: 5,
      lastRebuild: { at: new Date().toISOString(), reasons: ['history'] },
    })
    render(<PlanRebuildNotice />)

    expect(await screen.findByText(/there are new rides on record/)).toBeInTheDocument()
  })

  it('goes once seen, for this rebuild only', async () => {
    signIn()
    const at = new Date().toISOString()
    useAppStore.setState({ planRebuild: { at, reasons: ['sports'] } })
    const { unmount } = render(<PlanRebuildNotice />)

    await userEvent.click(screen.getByRole('button', { name: 'Got it' }))

    expect(screen.queryByText(/Your plan is rebuilt/)).not.toBeInTheDocument()
    expect(localStorage.getItem(seenKey(EMAIL))).toBe(at)
    unmount()

    useAppStore.setState({ planRebuild: { at: new Date(Date.now() + 1000).toISOString(), reasons: ['level'] } })
    render(<PlanRebuildNotice />)
    expect(screen.getByText(/your level changed/)).toBeInTheDocument()
  })

  it('says nothing about an old rebuild', () => {
    signIn()
    useAppStore.setState({
      planRebuild: { at: new Date(Date.now() - NOTICE_MAX_AGE_MS - 1000).toISOString(), reasons: ['ftp'] },
    })
    const { container } = render(<PlanRebuildNotice />)
    expect(container).toBeEmptyDOMElement()
  })

  it('says nothing without a rebuild, without an account, or when the server cannot be reached', async () => {
    signIn()
    mockChanged.mockRejectedValue(new Error('offline'))
    const { container, unmount } = render(<PlanRebuildNotice />)
    await waitFor(() => expect(mockChanged).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
    unmount()

    useAppStore.setState({ userProfile: null, planRebuild: { at: new Date().toISOString(), reasons: ['ftp'] } })
    const second = render(<PlanRebuildNotice />)
    expect(second.container).toBeEmptyDOMElement()
  })

  it('names a reason it has no words for by its code', () => {
    signIn()
    useAppStore.setState({ planRebuild: { at: new Date().toISOString(), reasons: ['something_new'] } })
    render(<PlanRebuildNotice />)
    expect(screen.getByText(/Your plan is rebuilt: something_new\./)).toBeInTheDocument()
  })

  it('has words for every reason the backend names, in German too', () => {
    expect(Object.keys(REASON_TEXT).sort()).toEqual(
      ['assumptions', 'ftp', 'history', 'hours', 'level', 'race', 'sports', 'training_days'].sort(),
    )
    useAppStore.setState({ uiLanguage: 'de' })
    signIn()
    useAppStore.setState({ planRebuild: { at: new Date().toISOString(), reasons: ['sports', 'level'] } })
    render(<PlanRebuildNotice />)
    expect(
      screen.getByText(/^Dein Plan ist neu gebaut: deine Sportarten haben sich geändert, dein Niveau hat sich geändert\./),
    ).toBeInTheDocument()
  })

  it('keeps it hidden in a private window that cannot store, for the visit', async () => {
    signIn()
    useAppStore.setState({ planRebuild: { at: new Date().toISOString(), reasons: ['race'] } })
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('denied')
    })
    render(<PlanRebuildNotice />)

    await userEvent.click(screen.getByRole('button', { name: 'Got it' }))

    expect(screen.queryByText(/Your plan is rebuilt/)).not.toBeInTheDocument()
    setItem.mockRestore()
  })
})
