import { afterEach, describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { browserLanguage, translate } from './index'
import { DE } from './de'
import WeekStrip from '../components/WeekStrip'
import { useAppStore } from '../store/useAppStore'

afterEach(() => {
  useAppStore.getState().setUiLanguage('auto')
  localStorage.clear()
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

describe('the interface language', () => {
  it('can be fixed to German in Settings and is kept in this browser', async () => {
    useAppStore.getState().setUiLanguage('de')

    render(<WeekStrip plan={[]} selectedDate="2026-10-15" onSelect={() => {}} onShowMore={() => {}} />)

    expect(screen.getByRole('heading', { name: 'Deine Woche' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Mehr anzeigen' })).toBeInTheDocument()
    expect(localStorage.getItem('ai_trainer_ui_language')).toBe('de')
  })

  it('follows the browser while it is on automatic', async () => {
    render(<WeekStrip plan={[]} selectedDate="2026-10-15" onSelect={() => {}} onShowMore={() => {}} />)

    // jsdom's browser speaks en-US.
    expect(screen.getByRole('heading', { name: 'Your week' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Show more' }))
  })
})
