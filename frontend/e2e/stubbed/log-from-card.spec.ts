/**
 * Logging today's session from the dashboard card (ai-trainer-ops#52).
 *
 * Three clicks deep before: "Show more", the calendar, the day, then the form.
 * Now one tap on the card, the form, and the card says done — and stays done
 * after a reload, which is the part only the real stack can show.
 */

import { expect, test, type ConsoleMessage, type Page } from '@playwright/test'

function newAthlete() {
  const stamp = `${Date.now()}-${Math.floor(Math.random() * 1e6)}`
  return {
    name: 'Robin Card',
    email: `e2e-card-${stamp}@aitrainer-e2e.com`,
    password: 'Str0ng!Passw0rd',
  }
}

function watchConsole(page: Page): string[] {
  const errors: string[] = []
  page.on('console', (message: ConsoleMessage) => {
    if (message.type() === 'error') errors.push(message.text())
  })
  page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`))
  return errors
}

async function onboard(page: Page, athlete: ReturnType<typeof newAthlete>) {
  await page.goto('/register')
  await page.getByPlaceholder('Your name').fill(athlete.name)
  await page.getByPlaceholder('you@example.com').fill(athlete.email)
  await page.getByPlaceholder('Strong password').fill(athlete.password)
  await page.getByPlaceholder('Repeat your password').fill(athlete.password)
  await page.getByRole('button', { name: 'Create Account' }).click()
  await expect(page.getByText(`Welcome, ${athlete.name}!`).first()).toBeVisible({ timeout: 30_000 })
  for (let step = 0; step < 4; step++) {
    await page.getByRole('button', { name: /^continue$/i }).first().click()
  }
  await page.getByRole('button', { name: /generate my 14-day training plan/i }).click()
  await expect(page.getByRole('heading', { name: /your week/i }).first()).toBeVisible({
    timeout: 60_000,
  })
}

test("today's session is logged from the card and stays done", async ({ page }) => {
  const errors = watchConsole(page)
  await onboard(page, newAthlete())

  await page.getByRole('button', { name: 'Log it' }).first().click()
  await page.getByRole('button', { name: /save workout/i }).click()

  await expect(page.getByText('Done', { exact: true }).first()).toBeVisible()
  await expect(page.getByRole('button', { name: 'Log it' })).toHaveCount(0)

  await page.reload()
  await expect(page.getByText('Done', { exact: true }).first()).toBeVisible()

  expect(errors, `console errors: ${errors.join(' | ')}`).toEqual([])
})
