import type { KeyboardEvent } from 'react'
import type { Translate } from '../i18n'

// The server's rule (`schemas.password_problem`), checked against the same cases
// (passwordCases.json), so the verdict on screen is the server's
// (ai-trainer-ops#114).
export const weakTerms = ['password', 'passwort', 'qwert', '12345', 'abcde', 'admin', 'welcome', 'trainlikea']

const PERSONAL_PART_MIN_CHARS = 4
// Letters and digits in any script: "Müller" stays one word.
const personalWord = /[\p{L}\p{N}]+/gu

const chars = (text: string) => [...text].length

/** The parts of the email and name a password must not contain, longest first.
 *
 * The local part, its pieces and the name's words, from four characters up. Not
 * the domain: "web" in anna@web.de refused "mein-web-kennwort-99".
 */
export function personalParts(name: string, email: string): string[] {
  const local = (email.split('@')[0] ?? '').toLowerCase().normalize('NFC')
  const words = name.toLowerCase().normalize('NFC')
  const candidates = new Set([local, ...(local.match(personalWord) ?? []), ...(words.match(personalWord) ?? [])])
  return [...candidates]
    .filter((part) => chars(part) >= PERSONAL_PART_MIN_CHARS)
    .sort((a, b) => chars(b) - chars(a) || (a < b ? -1 : a > b ? 1 : 0))
}

export interface PasswordCheck {
  id: 'length' | 'variety' | 'trivial'
  label: string
  vars?: Record<string, string>
  valid: boolean
}

/** The password rules, as checks shown while the athlete types.
 *
 * Shared by registration and the password reset (ai-trainer-ops#35). The reset
 * page knows no name or email; the server checks those there.
 */
export function getPasswordChecks(password: string, name: string, email: string): PasswordCheck[] {
  const lowerPassword = password.toLowerCase().normalize('NFC')
  const common = weakTerms.some((term) => lowerPassword.includes(term))
  const part = personalParts(name, email).find((candidate) => lowerPassword.includes(candidate))

  return [
    // Length plus a common/personal-word check, no composition rules (NIST SP 800-63B, ai-trainer-ops#44).
    { id: 'length', label: 'At least 10 characters (a short phrase works)', valid: chars(password) >= 10 },
    { id: 'variety', label: 'Not just a few repeated characters', valid: new Set(lowerPassword).size >= 5 },
    part && !common
      ? { id: 'trivial', label: 'Not “{part}” from your name or email', vars: { part }, valid: false }
      : { id: 'trivial', label: 'No common words, name, or email', valid: !common && !part },
  ]
}

const personalMessage = /^Password must not include “(.+)” from your name or email\.$/

/** A message from the server, in the athlete's language; the one that names a
 * part of the name or email keeps the part (ai-trainer-ops#114). */
export function serverPasswordMessage(t: Translate, message: string): string {
  const personal = personalMessage.exec(message)
  return personal
    ? t('Password must not include “{part}” from your name or email.', { part: personal[1] })
    : t(message)
}

/** Show a password only while its eye button is held, by pointer or key. */
export function revealWhileHeld(setVisible: (visible: boolean) => void) {
  return {
    onPointerDown: () => setVisible(true),
    onPointerUp: () => setVisible(false),
    onPointerLeave: () => setVisible(false),
    onPointerCancel: () => setVisible(false),
    onBlur: () => setVisible(false),
    onKeyDown: (event: KeyboardEvent<HTMLButtonElement>) => {
      if (event.key === ' ' || event.key === 'Enter') {
        event.preventDefault()
        setVisible(true)
      }
    },
    onKeyUp: (event: KeyboardEvent<HTMLButtonElement>) => {
      if (event.key === ' ' || event.key === 'Enter') {
        event.preventDefault()
        setVisible(false)
      }
    },
  }
}
