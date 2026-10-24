/** Every localStorage read and write of the app, in one place (ai-trainer-ops#269).

Storage can be unavailable (a private window, a blocked site), so every access
is guarded here rather than at each call site. A value that belongs to one
account is keyed by it: two friends on one browser read only their own "new
since your last login". Such a value is not cleared at sign-out, because the
next sign-in of the same account needs it. */

export function readLocal(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

export function writeLocal(key: string, value: string): void {
  try {
    localStorage.setItem(key, value)
  } catch {
    // Unavailable storage costs the convenience, never the page.
  }
}

/** ``name`` as one account's own key. */
export function accountKey(name: string, account: string): string {
  return `${name}:${account}`
}
