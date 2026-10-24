import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import PageLoadBoundary from './PageLoadBoundary'

const reloading = vi.hoisted(() => ({ now: false }))
vi.mock('../lib/chunkReload', () => ({ reloadingForChunk: () => reloading.now }))

function MissingChunk(): never {
  throw new TypeError('Failed to fetch dynamically imported module: /assets/SettingsPage-old.js')
}

describe('PageLoadBoundary (ai-trainer-ops#275)', () => {
  afterEach(() => {
    reloading.now = false
    vi.restoreAllMocks()
  })

  it('says the page did not load and offers to try again, instead of a blank app', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    const reload = vi.fn()

    render(
      <PageLoadBoundary reload={reload}>
        <MissingChunk />
      </PageLoadBoundary>,
    )

    expect(screen.getByRole('alert')).toHaveTextContent('This page could not be loaded')
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('shows nothing, not the error, while the page reloads for a new version (review of #1071)', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    reloading.now = true

    const { container } = render(
      <PageLoadBoundary reload={vi.fn()}>
        <MissingChunk />
      </PageLoadBoundary>,
    )

    expect(container).toBeEmptyDOMElement()
  })

  it('shows the page when it loads', () => {
    render(
      <PageLoadBoundary>
        <p>Settings</p>
      </PageLoadBoundary>,
    )

    expect(screen.getByText('Settings')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
