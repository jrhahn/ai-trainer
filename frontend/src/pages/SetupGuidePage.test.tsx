import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import SetupGuidePage from './SetupGuidePage'
import { setupGuideSection } from '../utils/links'

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/anleitung" element={<SetupGuidePage language="de" />} />
        <Route path="/guide" element={<SetupGuidePage language="en" />} />
      </Routes>
    </MemoryRouter>,
  )
}

/** The ids the page scrolls to (the test setup stubs scrollIntoView for jsdom). */
function recordScrolls(): string[] {
  const scrolled: string[] = []
  vi.spyOn(window.HTMLElement.prototype, 'scrollIntoView').mockImplementation(function (this: HTMLElement) {
    scrolled.push(this.id)
  })
  return scrolled
}

beforeEach(() => {
  // jsdom has no scrolling of the window.
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('the setup guide inside the app (ai-trainer-ops#305)', () => {
  it.each([
    ['/anleitung', 'Erste Schritte', 'de'],
    ['/guide', 'Getting started', 'en'],
  ])('%s shows the guide in its language', (path, title, language) => {
    const { container } = renderAt(path)

    expect(screen.getByRole('heading', { level: 1, name: title })).toBeInTheDocument()
    expect(container.querySelector('article')).toHaveAttribute('lang', language)
  })

  it.each(['/anleitung', '/guide'])('%s shows the five screenshots from the app itself', (path) => {
    renderAt(path)

    const sources = screen.getAllByRole('img').map((img) => img.getAttribute('src') ?? '')
    expect(sources).toHaveLength(5)
    for (const [i, name] of [
      '01-intervals-startseite',
      '02-verbindungen-garmin',
      '03-strava-import',
      '04-api-schluessel',
      '05-app-verbunden',
    ].entries()) {
      expect(sources[i]).toContain(name)
      expect(sources[i]).not.toMatch(/^https?:|github/)
    }
  })

  it.each([
    ['de', '/anleitung'],
    ['en', '/guide'],
  ] as const)('gives each section the id its link opens (%s)', (language, path) => {
    renderAt(path)

    for (const section of ['intervals', 'geminiKey'] as const) {
      const id = setupGuideSection(language, section).split('#')[1]
      expect(document.getElementById(id)?.tagName).toBe('H2')
    }
  })

  it('jumps to the section the link names, encoded or not', () => {
    const scrolled = recordScrolls()

    const { unmount } = renderAt('/anleitung#3-dein-ger%C3%A4t-mit-intervalsicu-verbinden')
    unmount()
    renderAt(setupGuideSection('en', 'geminiKey'))

    expect(scrolled).toEqual(['3-dein-gerät-mit-intervalsicu-verbinden', 'optional-your-own-google-gemini-key'])
  })

  it.each([
    ['it cannot read', '/guide#%E0%A4%A'],
    ['naming no section', '/guide#no-such-section'],
  ])('opens at the top for an address %s', (_case, path) => {
    const scrolled = recordScrolls()

    renderAt(path)

    expect(screen.getByRole('heading', { level: 1, name: 'Getting started' })).toBeInTheDocument()
    expect(scrolled).toEqual([])
    expect(window.scrollTo).toHaveBeenCalledWith(0, 0)
  })

  it('opens the other language at its top, not where this one was read (review of #1176)', async () => {
    renderAt('/anleitung')
    vi.mocked(window.scrollTo).mockClear()

    await userEvent.click(screen.getByRole('link', { name: 'Getting started' }))

    expect(screen.getByRole('heading', { level: 1, name: 'Getting started' })).toBeInTheDocument()
    expect(window.scrollTo).toHaveBeenCalledWith(0, 0)
  })

  it('opens other sites beside the app, and the other language inside it', () => {
    renderAt('/anleitung')

    const intervals = screen.getAllByRole('link', { name: 'https://intervals.icu' })[0]
    expect(intervals).toHaveAttribute('target', '_blank')
    expect(intervals).toHaveAttribute('rel', 'noreferrer')
    const english = screen.getByRole('link', { name: 'Getting started' })
    expect(english).toHaveAttribute('href', '/guide')
    expect(english).not.toHaveAttribute('target')
  })
})
