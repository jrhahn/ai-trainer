import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { english, translate, type Translate } from '../i18n'
import { DE } from '../i18n/de'
import { MEMORY_HEADINGS, shownMemory } from './coachMemory'

const german: Translate = (text, vars) => translate('de', text, vars)

describe('the headings of the coach’s notes (ai-trainer-ops#258)', () => {
  it('are the backend’s, each with a German name', () => {
    const source = readFileSync(resolve(__dirname, '../../../backend/services/coach_memory.py'), 'utf8')
    const block = source.slice(source.indexOf('HEADINGS = (')).split('\n)')[0]
    const backend = [...block.matchAll(/"([^"]+)"/g)].map((m) => m[1])

    expect(backend).toHaveLength(10)
    expect([...MEMORY_HEADINGS]).toEqual(backend)
    for (const heading of MEMORY_HEADINGS) expect(DE[heading], heading).toBeDefined()
  })

  it('shows each heading in German, with its marks, above the coach’s own words', () => {
    const notes = [
      'Schedule constraints:',
      '- Nur dienstags und samstags, dienstags 90 Minuten.',
      '## Fatigue & intensity response',
      '**Goals & motivations:** den Ötztaler unter 9 h fahren.',
      '1. Race calendar:',
      '- Recurring issues: keine bisher',
    ].join('\n')

    expect(shownMemory(notes, german)).toBe(
      [
        'Zeitliche Rahmenbedingungen:',
        '- Nur dienstags und samstags, dienstags 90 Minuten.',
        '## Ermüdung und Reaktion auf Intensität',
        '**Ziele und Motivation:** den Ötztaler unter 9 h fahren.',
        '1. Rennkalender:',
        '- Wiederkehrende Probleme: keine bisher',
      ].join('\n'),
    )
  })

  it('reads a heading as the backend does: in any case, and with "and" for "&"', () => {
    expect(shownMemory('FTP AND TARGET CONTEXT:\n- 265 W', german)).toBe('FTP und Zielwerte:\n- 265 W')
  })

  it('leaves a line that only begins like a heading', () => {
    const notes = 'Recurring issues with the left knee after long climbs.\nRace calendars help her plan.'

    expect(shownMemory(notes, german)).toBe(notes)
  })

  it('words the race calendar the app merges in, line by line', () => {
    const notes = [
      'Goals & motivations:',
      '- Ride the Ötztaler.',
      '',
      'Race calendar:',
      '- 2026-08-30 at 07:00: 120 km, 2000 m climbing',
      '- 2027-05-01: 42.2 km, 350 m climbing',
    ].join('\n')

    expect(shownMemory(notes, german)).toBe(
      [
        'Ziele und Motivation:',
        '- Ride the Ötztaler.',
        '',
        'Rennkalender:',
        '- 30.08.2026 um 07:00: 120 km, 2000 Höhenmeter',
        '- 01.05.2027: 42,2 km, 350 Höhenmeter',
      ].join('\n'),
    )
    expect(shownMemory(notes, english)).toBe(notes)
  })

  it('leaves a note that only looks like a race line', () => {
    const notes = '- 2026-08-30: rode 120 km with lots of climbing'

    expect(shownMemory(notes, german)).toBe(notes)
  })

  it('leaves English notes as the coach wrote them', () => {
    const notes = MEMORY_HEADINGS.map((heading) => `${heading}:\n- A note.`).join('\n')

    expect(shownMemory(notes, english)).toBe(notes)
  })
})
