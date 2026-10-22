import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import Layout from './Layout'

const mockUseImportProgress = vi.hoisted(() => vi.fn())
vi.mock('../hooks/useImportProgress', () => ({ useImportProgress: mockUseImportProgress }))
const mockPlanTranslationSync = vi.hoisted(() => vi.fn())
vi.mock('../hooks/usePlanTranslations', () => ({ usePlanTranslationSync: mockPlanTranslationSync }))
const mockLegalContact = vi.hoisted(() => vi.fn())
vi.mock('../hooks/useLegalContact', () => ({ useLegalContact: mockLegalContact }))
// The plan follows the athlete's changes from here (ai-trainer-ops#206); its
// own tests are in usePlanAutoRebuild.test.tsx.
const mockPlanAutoRebuild = vi.hoisted(() => vi.fn())
vi.mock('../hooks/usePlanAutoRebuild', () => ({ usePlanAutoRebuild: mockPlanAutoRebuild }))

const mockToggleExpertMode = vi.hoisted(() => vi.fn())
const mockLogout = vi.hoisted(() => vi.fn())
let expertMode = false
type StoreShape = {
  isExpertMode: boolean
  toggleExpertMode: () => void
  userProfile: { name: string } | null
  logout: () => void
}
vi.mock('../store/useAppStore', () => ({
  useAppStore: (selector: (s: StoreShape) => unknown) =>
    selector({
      isExpertMode: expertMode,
      toggleExpertMode: mockToggleExpertMode,
      userProfile: { name: 'Jane Rider' },
      logout: mockLogout,
    }),
}))

function renderLayout() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<div>Dashboard content</div>} />
        </Route>
      </Routes>
    </MemoryRouter>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  expertMode = false
  mockUseImportProgress.mockReturnValue({ status: 'idle', imported: 0, skipped: 0 })
})

describe('Layout', () => {
  it('renders the brand, navigation and routed content', () => {
    renderLayout()
    expect(screen.getAllByText('Train Like a Pro').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Coach').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Settings').length).toBeGreaterThan(0)
    expect(screen.getByText('Dashboard content')).toBeInTheDocument()
    // The plan's translations are kept current on every signed-in page (ai-trainer-ops#121).
    expect(mockPlanTranslationSync).toHaveBeenCalled()
    expect(mockPlanAutoRebuild).toHaveBeenCalled()
  })

  it('toggles expert mode when the Expert button is clicked', async () => {
    renderLayout()
    await userEvent.click(screen.getByRole('button', { name: 'Expert' }))
    expect(mockToggleExpertMode).toHaveBeenCalledTimes(1)
  })

  it('opens the mobile drawer, revealing a second navigation', async () => {
    renderLayout()
    expect(screen.getAllByText('Coach')).toHaveLength(1)
    await userEvent.click(screen.getByRole('button', { name: 'Toggle menu' }))
    expect(screen.getAllByText('Coach')).toHaveLength(2)
  })

  it('exposes the mobile drawer as an accessible dialog and closes it on Escape', async () => {
    renderLayout()
    await userEvent.click(screen.getByRole('button', { name: 'Toggle menu' }))
    const dialog = screen.getByRole('dialog', { name: 'Navigation menu' })
    expect(dialog).toHaveAttribute('aria-modal', 'true')

    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('dialog', { name: 'Navigation menu' })).not.toBeInTheDocument()
    expect(screen.getAllByText('Coach')).toHaveLength(1)
  })

  it('signs the user out from the shell', async () => {
    renderLayout()
    // Name is shown and sign-out is reachable directly from the sidebar chrome.
    expect(screen.getByText('Jane Rider')).toBeInTheDocument()
    await userEvent.click(screen.getAllByRole('button', { name: 'Sign out' })[0])
    expect(mockLogout).toHaveBeenCalledTimes(1)
  })

  it('closes the drawer when the overlay or a nav link is clicked', async () => {
    renderLayout()
    await userEvent.click(screen.getByRole('button', { name: 'Toggle menu' }))
    const dialog = screen.getByRole('dialog', { name: 'Navigation menu' })

    // Clicking a nav link inside the drawer closes it (and does not bubble to the overlay).
    await userEvent.click(within(dialog).getByText('Coach'))
    expect(screen.queryByRole('dialog', { name: 'Navigation menu' })).not.toBeInTheDocument()

    // Re-open and close via the backdrop overlay.
    await userEvent.click(screen.getByRole('button', { name: 'Toggle menu' }))
    const overlay = screen.getByRole('dialog', { name: 'Navigation menu' }).parentElement!
    await userEvent.click(overlay)
    expect(screen.queryByRole('dialog', { name: 'Navigation menu' })).not.toBeInTheDocument()

    // Re-open and close by tapping the (mobile) brand/home link.
    await userEvent.click(screen.getByRole('button', { name: 'Toggle menu' }))
    const brandLinks = screen.getAllByRole('link', { name: /Train Like a Pro/ })
    await userEvent.click(brandLinks[brandLinks.length - 1])
    expect(screen.queryByRole('dialog', { name: 'Navigation menu' })).not.toBeInTheDocument()
  })

  it('traps Tab focus within the open drawer', async () => {
    renderLayout()
    await userEvent.click(screen.getByRole('button', { name: 'Toggle menu' }))
    const dialog = screen.getByRole('dialog', { name: 'Navigation menu' })
    const focusables = Array.from(
      dialog.querySelectorAll<HTMLElement>('a[href], button:not([disabled])')
    )
    const first = focusables[0]
    const last = focusables[focusables.length - 1]

    // Focus moves into the drawer on open.
    expect(first).toHaveFocus()

    // Tab from the last focusable wraps back to the first, and vice versa.
    last.focus()
    await userEvent.tab()
    expect(first).toHaveFocus()

    first.focus()
    await userEvent.tab({ shift: true })
    expect(last).toHaveFocus()
  })

  it('dismisses the import toast via its close button', async () => {
    mockUseImportProgress.mockReturnValue({ status: 'running', imported: 0, skipped: 0 })
    const { rerender } = renderLayout()
    mockUseImportProgress.mockReturnValue({ status: 'done', imported: 4, skipped: 0 })
    rerender(
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<div>Dashboard content</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    )
    // No skipped part when nothing was skipped (ai-trainer-ops#121).
    expect(screen.getByText('History imported: 4 activities imported')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Dismiss' }))
    expect(screen.queryByText(/History imported/)).not.toBeInTheDocument()
  })

  it('shows an import-complete toast when progress transitions running -> done', () => {
    mockUseImportProgress.mockReturnValue({ status: 'running', imported: 0, skipped: 0 })
    const { rerender } = renderLayout()
    expect(screen.queryByText(/History imported/)).not.toBeInTheDocument()

    mockUseImportProgress.mockReturnValue({ status: 'done', imported: 12, skipped: 3 })
    rerender(
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<div>Dashboard content</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    )

    expect(
      screen.getByText('History imported: 12 activities imported, 3 skipped')
    ).toBeInTheDocument()
  })

  it('counts one imported activity in the singular', () => {
    mockUseImportProgress.mockReturnValue({ status: 'running', imported: 0, skipped: 0 })
    const { rerender } = renderLayout()
    mockUseImportProgress.mockReturnValue({ status: 'done', imported: 1, skipped: 0 })
    rerender(
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<div>Dashboard content</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    )
    expect(screen.getByText('History imported: 1 activity imported')).toBeInTheDocument()
  })

  it('offers a way to report a problem when the server names an address (ai-trainer-ops#15)', () => {
    mockLegalContact.mockReturnValue({ operatorName: 'Jane', contactEmail: 'help@example.com' })
    renderLayout()

    const report = screen.getAllByRole('link', { name: /report a problem/i })[0]
    expect(report.getAttribute('href')).toBe(
      `mailto:help@example.com?subject=${encodeURIComponent('Problem with Train Like a Pro')}`,
    )
    expect(screen.getAllByRole('link', { name: /privacy policy/i })[0]).toHaveAttribute('href', '/privacy')
  })

  it('shows no problem link without an address, but keeps the privacy policy', () => {
    mockLegalContact.mockReturnValue(null)
    renderLayout()

    expect(screen.queryByRole('link', { name: /report a problem/i })).not.toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: /privacy policy/i }).length).toBeGreaterThan(0)
  })

  it('links the legal notice from the app menu (ai-trainer-ops#1)', () => {
    mockLegalContact.mockReturnValue(null)
    renderLayout()

    expect(screen.getAllByRole('link', { name: /legal notice/i })[0]).toHaveAttribute('href', '/impressum')
  })
})
