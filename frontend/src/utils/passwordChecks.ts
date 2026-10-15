import type { KeyboardEvent } from 'react'

const weakTerms = ['password', 'passwort', 'qwert', '12345', 'abcde', 'admin', 'welcome', 'trainlikea']

/** The password rules, as checks shown while the athlete types.
 *
 * Shared by registration and the password reset (ai-trainer-ops#35). The
 * server applies the same rules (`schemas.password_problem`), including the
 * name and email the reset page does not know, so this is guidance and the
 * server is the check.
 */
export function getPasswordChecks(password: string, name: string, email: string) {
  const lowerPassword = password.toLowerCase()
  const emailLocal = email.split('@')[0]?.toLowerCase() ?? ''
  const nameParts = name.toLowerCase().split(/[^a-z0-9]+/).filter((part) => part.length >= 3)
  const personalParts = [emailLocal, ...emailLocal.split(/[^a-z0-9]+/), ...nameParts].filter((part) => part.length >= 3)

  return [
    // Length plus a common/personal-word check, no composition rules (NIST SP 800-63B, ai-trainer-ops#44).
    { id: 'length', label: 'At least 10 characters (a short phrase works)', valid: [...password].length >= 10 },
    { id: 'variety', label: 'Not just a few repeated characters', valid: new Set(lowerPassword).size >= 5 },
    {
      id: 'trivial',
      label: 'No common words, name, or email',
      valid:
        !weakTerms.some((term) => lowerPassword.includes(term)) &&
        !personalParts.some((part) => lowerPassword.includes(part)),
    },
  ]
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
