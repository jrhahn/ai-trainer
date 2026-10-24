/**
 * A key intervals.icu refuses is no connection (ai-trainer-ops#212).
 *
 * The review of 2026-10-23 typed a wrong key on step 4 and got "Mit
 * Intervals.icu verbunden" beside "hat diesen Schlüssel nicht angenommen", and
 * an "Analyse & Generate Plan" that could only fail. The backend now asks
 * intervals.icu before it stores a key and answers 403 for a refused one. The
 * stub stack has no intervals.icu, so that answer is mocked at the app's own
 * endpoint; what is checked is the screen the athlete sees.
 */

import { expect, test } from '@playwright/test'

function newAthlete() {
  const stamp = `${Date.now()}-${Math.floor(Math.random() * 1e6)}`
  return {
    name: 'Robin Key',
    email: `e2e-key-${stamp}@aitrainer-e2e.com`,
    password: 'Str0ng!Passw0rd',
  }
}

test('a refused intervals.icu key is not shown as connected', async ({ page }) => {
  const athlete = newAthlete()
  await page.goto('/register')
  await page.getByPlaceholder('Your name').fill(athlete.name)
  await page.getByPlaceholder('you@example.com').fill(athlete.email)
  await page.getByPlaceholder('Strong password').fill(athlete.password)
  await page.getByPlaceholder('Repeat your password').fill(athlete.password)
  await page.getByRole('checkbox', { name: /privacy policy/i }).check()
  await page.getByRole('button', { name: 'Create Account' }).click()
  await expect(page.getByText(`Welcome, ${athlete.name}!`).first()).toBeVisible({ timeout: 30_000 })

  await page.route('**/api/v1/intervals/connection', (route) =>
    route.request().method() === 'PUT'
      ? route.fulfill({ status: 403, json: { detail: 'Intervals.icu rejected the API key' } })
      : route.continue()
  )

  const next = page.getByRole('button', { name: /^continue$/i }).first()
  for (let step = 0; step < 3; step++) await next.click()

  // Step 4: the option's name is its whole card, so it is found by its own line.
  const intervals = page.getByRole('button', { name: 'Recommended. Analyses your last 7 rides', exact: false })
  await intervals.click()
  await expect(intervals).toHaveAttribute('aria-pressed', 'true')
  const key = page.getByLabel('API Key', { exact: true })
  await key.fill('typed-wrong')
  await page.getByRole('button', { name: 'Connect Intervals.icu', exact: true }).click()

  await expect(
    page.getByText('intervals.icu did not accept this key. Copy the whole API key again and paste it here.')
  ).toBeVisible()
  await expect(page.getByText('Connected to Intervals.icu')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Analyse & Generate Plan' })).toHaveCount(0)
  await expect(next).toBeDisabled()
  await expect(key).toHaveValue('typed-wrong')

  // Another way on, and nothing of the refusal follows it to the summary.
  const manual = page.getByRole('button', { name: 'enter sessions myself', exact: false })
  await manual.click()
  await expect(manual).toHaveAttribute('aria-pressed', 'true')
  await next.click()
  await expect(page.getByRole('button', { name: /generate my 14-day training plan/i })).toBeVisible()
  await expect(page.getByText(/did not accept this key|rejected the API key/i)).toHaveCount(0)
})
