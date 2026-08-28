import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import LandingPage, { openSourceFacts, pillars, steps } from './LandingPage'

function setup() {
  render(
    <MemoryRouter>
      <LandingPage />
    </MemoryRouter>
  )
}

function sentences(text: string): string[] {
  return text.split(/(?<=[.?!])\s+/).filter(Boolean)
}

describe('LandingPage', () => {
  it('leads with the coaching promise rather than metrics', () => {
    setup()
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(/coaching that keeps up with your week/i)
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
    for (const { body } of [...pillars, ...steps, ...openSourceFacts]) {
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

  it('names the differentiators: free, open source, own key, Strava via intervals.icu', () => {
    setup()
    expect(screen.getByText(/open source · free to run/i)).toBeInTheDocument()
    expect(screen.getAllByText(/google gemini key/i).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/agpl-3\.0/i).length).toBeGreaterThan(0)
    expect(screen.getByText(/intervals\.icu pulls your rides straight from strava/i)).toBeInTheDocument()
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
    expect(screen.getByText(/nutrition questions count too/i)).toBeInTheDocument()
  })
})
