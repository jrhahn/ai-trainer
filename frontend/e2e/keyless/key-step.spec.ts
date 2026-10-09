/**
 * A fresh athlete on a BYOK-only deployment is asked for a key
 * (ai-trainer-ops#41).
 *
 * This stack is the exact mode the dead end lived in: no provider key, no admin
 * fallback, which is also what the landing page promises ("you bring a Google
 * Gemini key"). Before this, onboarding walked all the way to "Generate My
 * 14-Day Training Plan", answered 402, and named a Settings page that is
 * unreachable until onboarding finishes.
 *
 * The companion assertion is in the stubbed stack, where no key is needed and
 * the step must not appear. Those two together are the whole of the behaviour:
 * one spec alone could be satisfied by a step that is always shown or never is.
 */

import { expect, test, type Page } from '@playwright/test'

function newAthlete() {
  const stamp = `${Date.now()}-${Math.floor(Math.random() * 1e6)}`
  return {
    name: 'Sam Byok',
    email: `e2e-byok-${stamp}@aitrainer-e2e.com`,
    password: 'Str0ng!Passw0rd',
  }
}

async function register(page: Page, athlete: ReturnType<typeof newAthlete>) {
  await page.goto('/register')
  await page.getByPlaceholder('Your name').fill(athlete.name)
  await page.getByPlaceholder('you@example.com').fill(athlete.email)
  await page.getByPlaceholder('Strong password').fill(athlete.password)
  await page.getByPlaceholder('Repeat your password').fill(athlete.password)
  await page.getByRole('button', { name: 'Create Account' }).click()
  await expect(page.getByText(`Welcome, ${athlete.name}!`).first()).toBeVisible({
    timeout: 30_000,
  })
}

test.describe('BYOK-only onboarding asks for a key', () => {
  test('counts six steps from the very first screen', async ({ page }) => {
    // Before the athlete has answered anything, so the requirement is not a
    // surprise sprung at the end.
    await register(page, newAthlete())

    await expect(page.getByText('Step 1 of 6')).toBeVisible()
  })

  test('puts the key step before the summary, not after the failure', async ({ page }) => {
    await register(page, newAthlete())

    for (let step = 0; step < 4; step++) {
      await page.getByRole('button', { name: /^continue$/i }).first().click()
    }

    // The key step, and *not* the summary: this is the ordering the issue asks
    // for. A "Generate" button that cannot work is the thing being removed.
    await expect(page.getByRole('heading', { name: /add your gemini key/i })).toBeVisible()
    await expect(page.getByText('Ready to Go!')).toHaveCount(0)
    await expect(page.getByText('Step 5 of 6')).toBeVisible()

    // Everything the issue's acceptance criteria name, on one screen.
    const guide = page.getByRole('link', { name: /get a key/i })
    await expect(guide).toHaveAttribute('href', 'https://aistudio.google.com/apikey')
    await expect(page.getByRole('button', { name: /test key/i })).toBeVisible()
    await expect(page.getByRole('button', { name: /save and continue/i })).toBeVisible()

    // And no way to skip it: Continue is gone or dead on this step.
    const skips = page.getByRole('button', { name: /^continue$/i })
    for (let i = 0; i < (await skips.count()); i++) {
      await expect(skips.nth(i)).toBeDisabled()
    }
  })

  test('tells the athlete when the key is refused, and keeps them on the step', async ({
    page,
  }) => {
    // A real round trip to a real provider with a key that cannot work. The
    // backend answers 422 from `POST /users/me/ai-key/test`; what is asserted
    // is that the browser shows it and nothing advances.
    await register(page, newAthlete())
    for (let step = 0; step < 4; step++) {
      await page.getByRole('button', { name: /^continue$/i }).first().click()
    }
    await expect(page.getByRole('heading', { name: /add your gemini key/i })).toBeVisible()

    await page.getByLabel(/gemini api key/i).fill('AIzaNotARealKeyAtAll')
    await page.getByRole('button', { name: /test key/i }).click()

    await expect(page.getByText(/validation failed|could not check/i)).toBeVisible({
      timeout: 60_000,
    })
    await expect(page.getByRole('heading', { name: /add your gemini key/i })).toBeVisible()
    await expect(page.getByText('Ready to Go!')).toHaveCount(0)
  })

  test('does not leave the account onboarded without a plan', async ({ page }) => {
    // The escape route from ai-trainer-ops#41: `isOnboarded` used to be written
    // before generation, so a reload landed on an empty dashboard saying "No
    // session planned for today" with nothing naming the cause.
    //
    // With the key step in place the athlete never reaches generation, so the
    // reload should bring them back into onboarding — at the key step, which is
    // the only screen that can move them forward.
    const athlete = newAthlete()
    await register(page, athlete)
    for (let step = 0; step < 4; step++) {
      await page.getByRole('button', { name: /^continue$/i }).first().click()
    }
    await expect(page.getByRole('heading', { name: /add your gemini key/i })).toBeVisible()

    await page.reload()

    // Still in onboarding, not on a dashboard with no plan.
    await expect(page.getByText(/step \d of 6/i)).toBeVisible({ timeout: 30_000 })
    await expect(page.getByText(/no session planned for today/i)).toHaveCount(0)
  })
})
