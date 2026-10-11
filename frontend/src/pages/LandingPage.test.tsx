import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import LandingPage, { facts, moments } from './LandingPage'

function setup() {
  return render(
    <MemoryRouter>
      <LandingPage />
    </MemoryRouter>
  )
}

function sentences(text: string): string[] {
  return text.split(/(?<=[.?!])\s+/).filter(Boolean)
}

describe('LandingPage', () => {
  it('leads with the coach knowing the rider rather than with metrics', () => {
    setup()
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(/a coach who already knows your riding/i)
  })

  // --- calm (ai-trainer-ops#67) --------------------------------------------
  // The owner's verdict on the previous page: too much text, too many widgets.
  // These pin the two things that made it so, so a later edit cannot quietly
  // grow it back.

  it('keeps the copy short', () => {
    const { container } = setup()
    const words = (container.textContent ?? '').split(/\s+/).filter(Boolean)
    // The previous page carried about 400 words; half of that is the ceiling.
    expect(words.length).toBeLessThan(200)
  })

  it('keeps the landscape out of the accessibility tree', () => {
    const { container } = setup()
    const svg = container.querySelector('svg.ride-landscape')
    expect(svg).toHaveAttribute('aria-hidden', 'true')
  })

  it('has one call to sign up in the page body, not a closing banner', () => {
    setup()
    expect(screen.getAllByRole('link', { name: /create a free account/i })).toHaveLength(1)
  })

  it('offers both sign-up and sign-in paths', () => {
    setup()
    expect(screen.getAllByRole('link', { name: /create a free account/i }).length).toBeGreaterThan(0)
    expect(screen.getAllByRole('link', { name: /^sign in$/i })[0]).toHaveAttribute('href', '/login')
    // Buttons say what they do rather than what they promise (#610).
    expect(screen.getByRole('link', { name: /^create account$/i })).toHaveAttribute('href', '/register')
  })

  // --- voice (#610) ----------------------------------------------------------
  // The copy said the right things in a register that gave itself away. These
  // guard the two tells that are mechanically checkable; the rest is taste.

  it('states a fact in every headline instead of setting up a contrast', () => {
    setup()
    for (const heading of screen.getAllByRole('heading')) {
      // "Not a spreadsheet of numbers. A coach you talk to." and its relatives.
      expect(heading.textContent).not.toMatch(/\bnot\b/i)
    }
  })

  it('keeps the feature copy off the rule of three', () => {
    for (const body of [...moments.map((m) => m.body), ...facts]) {
      // Three items in one breath, whether joined by commas ...
      for (const sentence of sentences(body)) {
        expect((sentence.match(/,/g) ?? []).length).toBeLessThan(2)
      }
      // ... or broken into a run of clipped sentences ("Missed Tuesday. Legs
      // flat. Work trip.").
      const clipped = sentences(body).filter((s) => s.split(/\s+/).length <= 4)
      expect(clipped.length).toBeLessThan(3)
    }
  })

  it('names the differentiators: free, source available, own key, Strava via intervals.icu', () => {
    setup()
    expect(screen.getByText(/free to use/i)).toBeInTheDocument()
    expect(screen.getByText(/source available/i)).toBeInTheDocument()
    expect(screen.getAllByText(/google gemini key/i).length).toBeGreaterThan(0)
    expect(screen.getByText(/strava through intervals\.icu/i)).toBeInTheDocument()
  })

  it('names the licence the code actually carries, and never calls it open source', () => {
    // The page said "AGPL-3.0" and "open source" after the code had moved to
    // the FSL, which the README calls "source available, not open source".
    const licence = readFileSync(resolve(__dirname, '../../../LICENSE'), 'utf8')
    const spdx = licence.match(/^FSL-[\d.]+-\w+$/m)?.[0]
    expect(spdx).toBeTruthy()

    const { container } = setup()
    const text = container.textContent ?? ''
    expect(text).toContain(spdx!)
    expect(text).not.toMatch(/open.source|AGPL/i)
  })

  it('offers entering sessions by hand as well as a FIT file', () => {
    setup()
    expect(screen.getByText(/upload a fit file or enter your sessions yourself/i)).toBeInTheDocument()
  })

  it('points new users at the setup guide', () => {
    setup()
    const guideLinks = screen
      .getAllByRole('link')
      .filter((link) => link.getAttribute('href')?.includes('docs/getting-started.md'))
    expect(guideLinks.length).toBeGreaterThan(0)
  })

  it('promises nutrition questions too', () => {
    setup()
    expect(screen.getByText(/ask what to eat/i)).toBeInTheDocument()
  })
})
