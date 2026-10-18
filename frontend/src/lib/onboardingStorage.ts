/** Where half-finished onboarding is parked, and clearing it (ops#130).

Shared so sign-out can remove it: progress left in sessionStorage used to
survive a sign-out and greet the next account on a shared device. */
export const ONBOARDING_STORAGE_KEY = 'ai_trainer_onboarding_progress'

export function clearOnboardingProgress(): void {
  try {
    sessionStorage.removeItem(ONBOARDING_STORAGE_KEY)
  } catch {
    // sessionStorage may be unavailable; ignore.
  }
}
