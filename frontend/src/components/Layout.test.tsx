import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import Layout from './Layout'

const mockUseImportProgress = vi.hoisted(() => vi.fn())
vi.mock('../hooks/useImportProgress', () => ({ useImportProgress: mockUseImportProgress }))

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
    expect(screen.getAllByText('Train Like a Pro!').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Coach').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Settings').length).toBeGreaterThan(0)
    expect(screen.getByText('Dashboard content')).toBeInTheDocument()
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
    const brandLinks = screen.getAllByRole('link', { name: /Train Like a Pro!/ })
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
    expect(screen.getByText(/Ride history imported/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Dismiss' }))
    expect(screen.queryByText(/Ride history imported/)).not.toBeInTheDocument()
  })

  it('shows an import-complete toast when progress transitions running -> done', () => {
    mockUseImportProgress.mockReturnValue({ status: 'running', imported: 0, skipped: 0 })
    const { rerender } = renderLayout()
    expect(screen.queryByText(/Ride history imported/)).not.toBeInTheDocument()

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
      screen.getByText('Ride history imported — 12 rides imported, 3 skipped')
    ).toBeInTheDocument()
  })
})
