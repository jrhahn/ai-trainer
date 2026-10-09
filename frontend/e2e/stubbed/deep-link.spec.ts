/**
 * A link to something other than the dashboard opens that thing
 * (ai-trainer-ops#57).
 *
 * Found by the onboarding spec next door, which tried to reach Settings by URL
 * and landed on the dashboard. The cause was not authorisation: the JWT is read
 * synchronously at start-up, but `isOnboarded` is not persisted and starts
 * `false`, so on the first render of a full page load the app held a session it
 * knew nothing about — and answered anyway. `path="*"` in the not-onboarded
 * branch replaced the URL, the profile then arrived, `/onboarding` was not in
 * the authenticated tree, and `path="*"` replaced it again. Two redirects and
 * the athlete's bookmark gone, all behind "Loading your training data…" because
 * the loading screen is an overlay beside `<Routes>` rather than instead of it.
 *
 * This is the browser half of the proof. The jsdom tests in `App.test.tsx`
 * assert that the URL survives the window; only a real page load shows that the
 * page the athlete asked for is the page they get.
 */

import { expect, test, type Page } from '@playwright/test'

/** Settings' name field, addressed by placeholder.
 *
 * Deliberately not addressed by its accessible name: that name arrives with the
 * ops#46 branch, and these two changes should not have to merge in a particular
 * order to stay green.
 */
const nameField = (page: Page) => page.getByPlaceholder('Your name')

function newAthlete() {
  const stamp = `${Date.now()}-${Math.floor(Math.random() * 1e6)}`
  return {
    name: 'Dev Linker',
    email: `e2e-deep-${stamp}@aitrainer-e2e.com`,
    password: 'Str0ng!Passw0rd',
  }
}

/** Register and walk onboarding to a finished account with a plan. */
async function onboard(page: Page, athlete: ReturnType<typeof newAthlete>) {
  await page.goto('/register')
  await page.getByPlaceholder('Your name').fill(athlete.name)
  await page.getByPlaceholder('you@example.com').fill(athlete.email)
  await page.getByPlaceholder('Strong password').fill(athlete.password)
  await page.getByPlaceholder('Repeat your password').fill(athlete.password)
  await page.getByRole('button', { name: 'Create Account' }).click()
  await expect(page.getByText(`Welcome, ${athlete.name}!`).first()).toBeVisible({
    timeout: 30_000,
  })
  for (let step = 0; step < 4; step++) {
    await page.getByRole('button', { name: /^continue$/i }).first().click()
  }
  await page.getByRole('button', { name: /generate my 14-day training plan/i }).click()
  await expect(
    page.getByRole('heading', { name: new RegExp(athlete.name.split(' ')[0], 'i') }).first()
  ).toBeVisible({ timeout: 60_000 })
}

test.describe('a link to a page other than the dashboard', () => {
  test('opens Settings when loaded directly, rather than the dashboard', async ({ page }) => {
    const athlete = newAthlete()
    await onboard(page, athlete)

    // A full browser navigation, which is what a bookmark, a shared link and a
    // reload all are. The SPA path through the nav always worked, which is why
    // this survived so long.
    await page.goto('/settings')

    // Positively: the page is there, and it is the athlete's own account.
    await expect(nameField(page)).toHaveValue(athlete.name, {
      timeout: 30_000,
    })
    await expect(page.getByText(`Signed in as ${athlete.email}`)).toBeVisible()
    expect(new URL(page.url()).pathname).toBe('/settings')
  })

  test('survives a reload of that page', async ({ page }) => {
    const athlete = newAthlete()
    await onboard(page, athlete)
    await page.goto('/settings')
    await expect(nameField(page)).toHaveValue(athlete.name, {
      timeout: 30_000,
    })

    await page.reload()

    await expect(nameField(page)).toHaveValue(athlete.name, {
      timeout: 30_000,
    })
    expect(new URL(page.url()).pathname).toBe('/settings')
  })

  test('sends a visitor with no session to the landing page, not into the app', async ({
    page,
  }) => {
    // The gate must not become a way in. Nothing is signed in here, so the same
    // URL has to end at the front door.
    await page.goto('/settings')

    await expect(page.getByRole('link', { name: /sign in|log in/i }).first()).toBeVisible({
      timeout: 30_000,
    })
    expect(new URL(page.url()).pathname).toBe('/')
  })
})
