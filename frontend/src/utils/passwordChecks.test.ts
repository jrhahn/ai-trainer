import { describe, expect, it, vi } from 'vitest'
import type { KeyboardEvent } from 'react'
import { english, translate } from '../i18n'
import { getPasswordChecks, personalParts, revealWhileHeld, serverPasswordMessage } from './passwordChecks'
import cases from './passwordCases.json'

const key = (k: string) => ({ key: k, preventDefault: vi.fn() }) as unknown as KeyboardEvent<HTMLButtonElement>

describe('getPasswordChecks', () => {
  it('passes a long phrase and fails a short or personal one', () => {
    expect(getPasswordChecks('gravel climbs at dawn', '', '').every((c) => c.valid)).toBe(true)
    expect(getPasswordChecks('short', '', '').find((c) => c.id === 'length')?.valid).toBe(false)
    expect(getPasswordChecks('robin rides a lot', 'Robin', '').find((c) => c.id === 'trivial')?.valid).toBe(false)
  })
})

// The same cases the server is tested on (backend/tests/test_password_rule.py), so
// the two cannot drift apart again (ai-trainer-ops#114).
const FAILS = { length: 'length', variety: 'variety', common: 'trivial', personal: 'trivial' } as const

describe('the shared password cases', () => {
  it.each(cases)('$password for "$name" <$email>: $problem', ({ password, name, email, problem, part }) => {
    const failing = getPasswordChecks(password, name, email).filter((check) => !check.valid)

    if (problem === null) {
      expect(failing).toEqual([])
      return
    }
    expect(failing.map((check) => check.id)).toEqual([FAILS[problem as keyof typeof FAILS]])
    expect(failing[0].vars?.part).toBe(problem === 'personal' ? part : undefined)
  })
})

describe('personalParts', () => {
  it('leaves the mail domain out and names the longest part first', () => {
    expect(personalParts('Jörg Müller-Lüdenscheidt', 'jm@t-online.de')).toEqual(['lüdenscheidt', 'müller', 'jörg'])
  })
})

describe('serverPasswordMessage', () => {
  it('translates the fixed messages and keeps the part the server named', () => {
    const german = (text: string, vars?: Record<string, string | number>) =>
      text === 'Password must not include “{part}” from your name or email.'
        ? `Das Passwort darf „${vars?.part}“ aus deinem Namen oder deiner E-Mail nicht enthalten.`
        : `de:${text}`

    expect(serverPasswordMessage(german, 'Password must not include “anna” from your name or email.')).toBe(
      'Das Passwort darf „anna“ aus deinem Namen oder deiner E-Mail nicht enthalten.',
    )
    expect(serverPasswordMessage(german, 'Password must be at least 10 characters.')).toBe(
      'de:Password must be at least 10 characters.',
    )
    expect(serverPasswordMessage(english, 'Registration failed')).toBe('Registration failed')
  })
})

describe('revealWhileHeld', () => {
  it('shows the password only while the pointer or the key is held', () => {
    const setVisible = vi.fn()
    const handlers = revealWhileHeld(setVisible)

    handlers.onPointerDown()
    handlers.onPointerUp()
    handlers.onPointerLeave()
    handlers.onPointerCancel()
    handlers.onBlur()
    handlers.onKeyDown(key(' '))
    handlers.onKeyUp(key('Enter'))
    handlers.onKeyDown(key('a'))

    expect(setVisible.mock.calls.map(([visible]) => visible)).toEqual([true, false, false, false, false, true, false])
  })
})

describe('serverPasswordMessage in German (ai-trainer-ops#268)', () => {
  it('translates a message the sign-up throws, such as a captcha that could not be solved', () => {
    const german = (key: string, values?: Record<string, string | number>) => translate('de', key, values)
    expect(serverPasswordMessage(german, 'Could not solve the captcha challenge')).toBe(
      'Die Sicherheitsabfrage ließ sich nicht lösen',
    )
  })
})

