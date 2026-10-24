import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import PageLoadBoundary from './PageLoadBoundary'

function MissingChunk(): never {
  throw new TypeError('Failed to fetch dynamically imported module: /assets/SettingsPage-old.js')
}

describe('PageLoadBoundary (ai-trainer-ops#275)', () => {
  afterEach(() => vi.restoreAllMocks())

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
