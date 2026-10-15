import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { browserLanguage, formatLongDate, localeTag, slots, translate } from './index'
import { DE } from './de'
import WeekStrip from '../components/WeekStrip'
import WorkoutFeedbackForm from '../components/WorkoutFeedbackForm'
import DayActivities from '../components/DayActivities'
import { useAppStore } from '../store/useAppStore'

afterEach(() => {
  useAppStore.getState().setUiLanguage('auto')
  localStorage.clear()
  vi.restoreAllMocks()
})

describe('browserLanguage (ai-trainer-ops#103 Q27)', () => {
  it('is German when the browser prefers German before English', () => {
    expect(browserLanguage(['de-AT', 'en-US'])).toBe('de')
    expect(browserLanguage(['fr-FR', 'de-DE'])).toBe('de')
  })

  it('is English when the browser prefers English, or neither', () => {
    expect(browserLanguage(['en-GB', 'de-DE'])).toBe('en')
    expect(browserLanguage(['fr-FR'])).toBe('en')
    expect(browserLanguage([])).toBe('en')
  })
})

describe('translate', () => {
  it('fills placeholders, and keeps English where nothing is translated yet', () => {
    expect(translate('de', 'The next {count} weeks', { count: 11 })).toBe('Die nächsten 11 Wochen')
    expect(translate('en', 'The next {count} weeks', { count: 11 })).toBe('The next 11 weeks')
    expect(translate('de', 'A sentence nobody has translated')).toBe('A sentence nobody has translated')
  })

  it('has the same placeholders in every German entry as in its English key', () => {
    const names = (text: string) => [...text.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort()
    for (const [english, german] of Object.entries(DE)) {
      expect(names(german), english).toEqual(names(english))
    }
  })
})

describe('slots', () => {
  it('puts elements where a translated sentence has its slots', () => {
    const { container } = render(
      <p>
        {slots(translate('de', 'Open {link} and scroll down to {section}.'), {
          link: <a key="link">deine intervals.icu-Einstellungen</a>,
          section: <strong key="section">Developer Settings</strong>,
        })}
      </p>,
    )

    expect(container.textContent).toBe('Öffne deine intervals.icu-Einstellungen und scroll nach unten zu Developer Settings.')
    expect(screen.getByText('Developer Settings').tagName).toBe('STRONG')
  })
})

describe('the interface language', () => {
  it('can be fixed to German in Settings and is kept in this browser', async () => {
    useAppStore.getState().setUiLanguage('de')

    render(<WeekStrip plan={[]} selectedDate="2026-10-15" onSelect={() => {}} onShowMore={() => {}} />)

    expect(screen.getByRole('heading', { name: 'Deine Woche' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Mehr anzeigen' })).toBeInTheDocument()
    expect(localStorage.getItem('ai_trainer_ui_language')).toBe('de')
  })

  it('follows the browser while it is on automatic', () => {
    render(<WeekStrip plan={[]} selectedDate="2026-10-15" onSelect={() => {}} onShowMore={() => {}} />)

    // jsdom's browser speaks en-US.
    expect(screen.getByRole('heading', { name: 'Your week' })).toBeInTheDocument()
  })

  it('speaks German on automatic when the browser prefers German', () => {
    vi.spyOn(navigator, 'languages', 'get').mockReturnValue(['de-DE', 'en-US'])

    render(<WeekStrip plan={[]} selectedDate="2026-10-15" onSelect={() => {}} onShowMore={() => {}} />)

    expect(screen.getByRole('heading', { name: 'Deine Woche' })).toBeInTheDocument()
  })
})


describe('localeTag', () => {
  it("keeps the browser's own English for dates, and German for German", () => {
    expect(localeTag('en', ['en-US', 'de-DE'])).toBe('en-US')
    expect(localeTag('en', ['de-DE'])).toBe('en-GB')
    expect(localeTag('de', ['en-US'])).toBe('de-DE')
  })
})


describe('formatLongDate', () => {
  it('writes the dashboard date in the interface language', () => {
    const day = new Date(2026, 9, 15)
    expect(formatLongDate(day, 'de')).toBe('Donnerstag, 15. Oktober 2026')
    expect(formatLongDate(day, 'en')).toBe('Thursday, October 15, 2026')
  })
})

describe('translate, on keys that are not UI text', () => {
  it("never resolves to one of Object's own members", () => {
    expect(translate('de', 'constructor')).toBe('constructor')
    expect(translate('de', 'toString')).toBe('toString')
  })
})

describe('logging in German (ai-trainer-ops#104)', () => {
  it('names the form, the fields and every effort level in German', () => {
    useAppStore.getState().setUiLanguage('de')
    const day = { date: '2026-10-15', workoutType: 'strength', title: 'Kraft', description: '', durationMinutes: 45 } as const

    render(<WorkoutFeedbackForm day={day} onSubmit={() => {}} onCancel={() => {}} />)

    expect(screen.getByRole('heading', { name: 'Krafteinheit eintragen' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '2 von 5 — Kontrolliert' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Einheit speichern' })).toBeInTheDocument()
  })

  it('names a synced sport type in German, and leaves one it does not know as it was', () => {
    useAppStore.getState().setUiLanguage('de')
    useAppStore.setState({
      rideMetricsHistory: [
        { stravaActivityId: 1, activityDate: '2026-10-15', sportType: 'VirtualRide', activityName: 'Zwift', durationSeconds: 3600 },
        { stravaActivityId: 2, activityDate: '2026-10-15', sportType: 'Kitesurf', activityName: 'Ostsee', durationSeconds: 3600 },
      ],
    })

    render(<DayActivities date="2026-10-15" />)

    expect(screen.getByRole('heading', { name: 'Was du an diesem Tag gemacht hast' })).toBeInTheDocument()
    expect(screen.getByText('virtuelle Radfahrt')).toBeInTheDocument()
    expect(screen.getByText('kitesurf')).toBeInTheDocument()
    useAppStore.setState({ rideMetricsHistory: [] })
  })
})

describe('the German dictionary', () => {
  it('has an entry for every literal text the interface passes to t()', () => {
    // A key missing from de.ts shows as English on a German page and fails no
    // other test, so every t('…') literal in the source is checked here.
    const sources = import.meta.glob(['../**/*.{ts,tsx}', '!../**/*.test.{ts,tsx}'], {
      query: '?raw',
      import: 'default',
      eager: true,
    }) as Record<string, string>
    const missing: string[] = []
    let checked = 0
    for (const [path, code] of Object.entries(sources)) {
      for (const match of code.matchAll(/\bt\(\s*(['"])((?:\\.|(?!\1)[^\\])*)\1/g)) {
        const key = match[2].replace(/\\(['"\\])/g, '$1')
        checked += 1
        if (!Object.hasOwn(DE, key)) missing.push(`${path}: ${key}`)
      }
    }
    expect(missing).toEqual([])
    // An empty glob or a regex that stopped matching would pass the line above.
    expect(checked).toBeGreaterThan(500)
  })
})
