import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import LandingPage, { facts, moments } from './LandingPage'
import { highlights } from '../components/AuthShell'
import { DE } from '../i18n/de'
import { useAppStore } from '../store/useAppStore'

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
  it('links the legal notice and the privacy policy from its footer (ai-trainer-ops#1)', () => {
    setup()
    expect(screen.getByRole('link', { name: 'Legal notice' })).toHaveAttribute('href', '/impressum')
    expect(screen.getByRole('link', { name: 'Privacy policy' })).toHaveAttribute('href', '/privacy')
  })

  it('leads with the coach knowing the rider rather than with metrics', () => {
    setup()
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(/a coach who already knows your riding/i)
  })

  it('says once, near the top, that runs and strength are planned too', () => {
    // ai-trainer-ops#53 item 1, #39 Q13: multisport since #709, said once; the
    // example stays a ride.
    const { container } = setup()
    const top = container.querySelector('h1')?.nextElementSibling?.textContent ?? ''
    expect(top).toMatch(/runs and strength/i)
    expect((container.textContent ?? '').match(/strength/gi)).toHaveLength(1)
  })

  it('shows the real dashboard, and the picture it shows exists', () => {
    // ai-trainer-ops#53 item 4, #81 Q19. The file is written by
    // e2e/stubbed/landing-screenshot.spec.ts with UPDATE_LANDING_SCREENSHOT=1.
    setup()
    const picture = screen.getByRole('img', { name: /dashboard/i })
    expect(picture).toHaveAttribute('src', '/landing-dashboard.png')
    expect(readFileSync(resolve(__dirname, '../../public/landing-dashboard.png')).length).toBeGreaterThan(10_000)
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

  it('asks for no Gemini key and names no Strava source before the dashboard (ai-trainer-ops#218)', () => {
    // A friend with the invitation reads these first: the sign-in and sign-up
    // highlights and the landing facts, in both languages.
    for (const text of [...highlights.map((h) => h.label), ...facts]) {
      for (const shown of [text, DE[text] ?? '']) {
        expect(shown, text).not.toMatch(/Your Gemini key|You bring a Gemini key|Dein Gemini-Schlüssel|Gemini-Schlüssel mit/)
        expect(shown, text).not.toMatch(/from Strava through|or Strava\b|von Strava über|oder Strava\b/)
      }
    }
  })

  it('promises the four onboarding questions, not "nothing to fill in"', () => {
    setup()
    expect(screen.getByText(/Four short questions, and you're set\./)).toBeInTheDocument()
    expect(screen.queryByText(/nothing to fill in/i)).toBeNull()
  })

  it('calls the coach "your coach", never "it" (ai-trainer-ops#211)', () => {
    // "Talk to it" made the coach sound like a gadget. The page never names
    // the coach in those lines, so coachNotIt.test.ts cannot see them. This
    // catches the coach as subject or object here: a sentence or headline
    // opening on "It", or talking to it.
    const { container } = setup()
    const blocks = [...container.querySelectorAll('h1, h2, p, blockquote')].map((el) => el.textContent ?? '')
    for (const text of blocks) {
      for (const sentence of sentences(text)) {
        expect(sentence).not.toMatch(/^It\b/)
      }
      expect(text).not.toMatch(/\b(talk to|tell|ask|with) it\b/i)
    }
    expect(screen.getAllByText(/your coach/i).length).toBeGreaterThan(1)
  })

  it('names the differentiators: free, source available, own key, rides through intervals.icu', () => {
    setup()
    expect(screen.getByText(/free to use/i)).toBeInTheDocument()
    expect(screen.getByText(/source available/i)).toBeInTheDocument()
    expect(screen.getAllByText(/google gemini key/i).length).toBeGreaterThan(0)
    // Not "Strava through intervals.icu": intervals.icu may not pass on rides it
    // got from Strava (owner, 2026-10-21; ai-trainer-ops#218).
    expect(screen.getByText(/from your device through intervals\.icu/i)).toBeInTheDocument()
    expect(screen.getByText(/strava-only rides do not reach us/i)).toBeInTheDocument()
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

describe('LandingPage in German (ai-trainer-ops#104)', () => {
  it('speaks German when the interface is German', () => {
    useAppStore.setState({ uiLanguage: 'de' })
    try {
      setup()
      expect(screen.getByRole('heading', { level: 1, name: 'Ein Coach, der weiß, wie du fährst.' })).toBeInTheDocument()
      expect(screen.getByRole('heading', { name: 'Dein Coach fragt nur, was zählt' })).toBeInTheDocument()
      expect(screen.getByText('Kostenlos. Das Coaching läuft bis zu einem Kontingent je 30 Tage auf unserem Google-Gemini-Schlüssel. Mit eigenem Schlüssel gibt es keine Grenze.')).toBeInTheDocument()
    } finally {
      useAppStore.setState({ uiLanguage: 'auto' })
    }
  })
})
