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

  it('leaves a message no pattern knows as it is', () => {
    expect(translate('de', 'Something else entirely, 42 times')).toBe('Something else entirely, 42 times')
  })
})
