import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { translate } from './index'

// Server messages with numbers in them (ai-trainer-ops#121, slice 3).
describe('server messages with numbers, in German', () => {
  const allowance =
    'You have used the free coaching allowance: 5,000,000 of 5,000,000 tokens in the last 30 days. ' +
    'Add your own AI key (Gemini or OpenAI) in Settings to keep going without a limit, or get in touch ' +
    'with whoever runs this coach. The allowance frees up again as older usage ages out of the 30-day window.'

  it('words the spent allowance with German numbers', () => {
    const german = translate('de', allowance)
    expect(german).toMatch(/^Du hast das freie Coaching-Kontingent aufgebraucht: 5\.000\.000 von 5\.000\.000 Tokens in den letzten 30 Tagen\./)
    expect(german).toContain('30-Tage-Fenster')
    expect(translate('en', allowance)).toBe(allowance)
  })

  it('falls through to English when the two day counts disagree', () => {
    // The window length must be the same number as "the last N days"; a backend
    // that ever split them would print a wrong German number otherwise.
    const mismatched = allowance.replace('30-day window', '7-day window')
    expect(translate('de', mismatched)).toBe(mismatched)
  })

  it('is still the sentence the backend writes, word for word', () => {
    // token_accounting.py builds it from f-string parts; each fixed part must stay.
    // These source checks are deliberately literal: reformatting the backend (a
    // changed quote style, a parenthesised concatenation) fails them, and the
    // failing `part` names what to look at.
    const source = readFileSync(resolve(__dirname, '../../../backend/services/token_accounting.py'), 'utf8')
      .replace(/(["'])\s*\n\s*f?\1/g, '')
    for (const part of [
      'You have used the free coaching allowance: ',
      ' tokens in the last ',
      ' days. Add your own AI key (Gemini or OpenAI) in Settings to keep going without a limit, or get in touch with whoever runs this coach. The allowance frees up again as older usage ages out of the ',
      '-day window.',
    ]) {
      expect(source, part).toContain(part)
    }
  })

  it('words the password reset reply, minutes included, as the auth router writes it', () => {
    const reply = 'If this address has an account, a link to reset the password is on its way. It works for 60 minutes.'
    expect(translate('de', reply)).toBe(
      'Wenn zu dieser Adresse ein Konto gehört, ist ein Link zum Zurücksetzen des Passworts unterwegs. Er gilt 60 Minuten.'
    )
    const source = readFileSync(resolve(__dirname, '../../../backend/routers/auth_router.py'), 'utf8')
      .replace(/(["'])\s*\n\s*f?\1/g, '')
    expect(source).toContain('If this address has an account, a link to reset the password is on its way. It works for ')
    expect(source).toContain(' minutes.')
    expect(translate('de', reply.replace('60 minutes', '1 minutes'))).toMatch(/Er gilt 1 Minute\.$/)
    // A backend that fixes its own plural keeps matching.
    expect(translate('de', reply.replace('60 minutes', '1 minute'))).toMatch(/Er gilt 1 Minute\.$/)
  })

  it('words the limits the chat stores, every session and a weekday (ai-trainer-ops#185, #231)', () => {
    expect(translate('de', 'At most 60 min per session')).toBe('Höchstens 60 min pro Einheit')
    expect(translate('de', 'At most 60 min on Tuesdays')).toBe('Dienstags höchstens 60 min')
    expect(translate('de', 'At most 45 min on Saturdays')).toBe('Samstags höchstens 45 min')
    const source = readFileSync(resolve(__dirname, '../../../backend/services/availability.py'), 'utf8')
    expect(source).toContain('return f"At most {minutes} min per session"')
    expect(source).toContain('return f"At most {minutes} min on {weekday.capitalize()}s"')
  })

  it('words the pinned and freed days and the goals the chat stores (ai-trainer-ops#265)', () => {
    expect(translate('de', 'Long ride on Saturday 2026-10-31')).toBe('Lange Ausfahrt am Samstag, 31.10.')
    expect(translate('de', 'Long ride on Sunday 2026-11-01, at least 180 min')).toBe(
      'Lange Ausfahrt am Sonntag, 01.11., mindestens 180 min',
    )
    expect(translate('de', 'No training on Wednesday 2026-11-04')).toBe('Kein Training am Mittwoch, 04.11.')
    expect(translate('de', '4,000 m of climbing in one ride')).toBe('4.000 Höhenmeter in einer Fahrt')
    expect(translate('de', '12,500 m of climbing in one week')).toBe('12.500 Höhenmeter in einer Woche')
    expect(translate('de', '200 km in one week')).toBe('200 km in einer Woche')
    expect(translate('de', '160 km in one ride')).toBe('160 km in einer Fahrt')
    const availability = readFileSync(resolve(__dirname, '../../../backend/services/availability.py'), 'utf8')
    expect(availability).toContain('when = f"{WEEKDAY_LABELS[day.weekday()].capitalize()} {day.isoformat()}"')
    expect(availability).toContain('return f"Long ride on {when}" + (f", at least {minutes} min" if minutes else "")')
    expect(availability).toContain('return f"No training on {when}"')
    const goals = readFileSync(resolve(__dirname, '../../../backend/services/goal_progress.py'), 'utf8')
    expect(goals).toContain('return f"{value:,.0f} m" if metric == ELEVATION else f"{value:,.0f} km"')
    expect(goals).toContain('what = "of climbing" if metric == ELEVATION else ""')
    expect(goals).toContain('where = "in one ride" if scope == RIDE else "in one week"')
  })

  it('words a limit for the next such day only (ai-trainer-ops#295)', () => {
    expect(translate('de', 'At most 120 min on Saturday 2026-10-31 only')).toBe('Nur Samstag, 31.10.: höchstens 120 min')
    expect(translate('de', 'At most 60 min on Tuesday 2026-11-03 only')).toBe('Nur Dienstag, 03.11.: höchstens 60 min')
    const source = readFileSync(resolve(__dirname, '../../../backend/services/availability.py'), 'utf8')
    expect(source).toContain('return f"At most {minutes} min on {weekday.capitalize()} {constraint_date} only"')
  })

  it('words "plan day N is invalid" as users.py writes it', () => {
    const source = readFileSync(resolve(__dirname, '../../../backend/routers/users.py'), 'utf8')
    expect(source).toContain('plan day {index} is invalid')
    expect(translate('de', 'plan day 3 is invalid')).toBe('Plantag 3 ist ungültig.')
  })

  it.each([
    ['Input should be less than or equal to 700', 'Der Wert darf höchstens 700 sein.'],
    ['Input should be greater than or equal to 1', 'Der Wert muss mindestens 1 sein.'],
    ['Input should be less than 5', 'Der Wert muss kleiner als 5 sein.'],
    ['Input should be greater than 0', 'Der Wert muss größer als 0 sein.'],
    ['Input should be less than or equal to 0.5', 'Der Wert darf höchstens 0,5 sein.'],
    ['String should have at most 2000 characters', 'Höchstens 2000 Zeichen.'],
    ['String should have at least 1 character', 'Mindestens 1 Zeichen.'],
    ['Field required', 'Dieses Feld fehlt.'],
    ['Input should be a valid integer, unable to parse string as an integer', 'Bitte eine ganze Zahl eingeben.'],
    ['Input should be a valid number, unable to parse string as a number', 'Bitte eine Zahl eingeben.'],
    ['plan day 3 is invalid', 'Plantag 3 ist ungültig.'],
    ['Input should be a valid string', 'Bitte einen Text eingeben.'],
    ['Input should be a valid boolean, unable to interpret input', 'Bitte ja oder nein wählen.'],
    ['value is not a valid email address: An email address must have an @-sign.', 'Das ist keine gültige E-Mail-Adresse.'],
    ["String should match pattern '^\\d{4}-\\d{2}-\\d{2}$'", 'Das Format stimmt nicht.'],
    ["Input should be 'cycling', 'running' or 'strength'", 'Bitte einen der angebotenen Werte wählen.'],
  ])('translates "%s"', (english, german) => {
    expect(translate('de', english)).toBe(german)
  })

  it('words a refused key with its provider, as llm.py writes it', () => {
    const refused = 'The AI provider (gemini) refused the API key. If you added your own key, check it in Settings → Your AI Provider Key.'
    expect(translate('de', refused)).toBe(
      'Der KI-Anbieter (gemini) hat den API-Schlüssel abgelehnt. Wenn du einen eigenen Schlüssel hinterlegt hast, prüf ihn unter Coach & KI → Dein KI-Schlüssel.'
    )
    const source = readFileSync(resolve(__dirname, '../../../backend/services/llm.py'), 'utf8').replace(/(["'])\s*\n\s*f?\1/g, '')
    expect(source).toContain('refused the API key. If you added your own key, check it in Settings → Your AI Provider Key.')
  })

  it('leaves a message no pattern knows as it is', () => {
    expect(translate('de', 'Something else entirely, 42 times')).toBe('Something else entirely, 42 times')
  })
})
