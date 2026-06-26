import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import Layout from './Layout'

const mockUseImportProgress = vi.hoisted(() => vi.fn())
vi.mock('../hooks/useImportProgress', () => ({ useImportProgress: mockUseImportProgress }))

const mockToggleExpertMode = vi.hoisted(() => vi.fn())
let expertMode = false
vi.mock('../store/useAppStore', () => ({
  useAppStore: (selector: (s: { isExpertMode: boolean; toggleExpertMode: () => void }) => unknown) =>
    selector({ isExpertMode: expertMode, toggleExpertMode: mockToggleExpertMode }),
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
