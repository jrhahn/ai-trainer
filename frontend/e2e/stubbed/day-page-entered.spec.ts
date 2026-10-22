/**
 * An activity entered for a day before the plan began, seen and changed on its
 * day page (ai-trainer-ops#207).
 *
 * A new athlete gives the coach a history by entering past sessions by hand.
 * Those days were never in the plan, and the day page showed only the sport
 * and the duration of what was entered, said the day "is no longer part of your
 * current plan", and could not change the entry. This runs the whole loop:
 * enter, open the day, see what was entered, change it, see the change.
 */

import { expect, test, type ConsoleMessage, type Page } from '@playwright/test'

function newAthlete() {
  const stamp = `${Date.now()}-${Math.floor(Math.random() * 1e6)}`
  return {
    name: 'Kim History',
    email: `e2e-day-${stamp}@aitrainer-e2e.com`,
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
  await page.getByRole('checkbox', { name: /privacy policy/i }).check()
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

/** A local calendar date `days` before today, as the date input takes it. */
function daysAgo(days: number): string {
  const d = new Date()
  d.setDate(d.getDate() - days)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

test('a run entered before sign-up shows what was entered on its day, and changes there', async ({ page }) => {
  const errors = watchConsole(page)
  await onboard(page, newAthlete())
  const day = daysAgo(30)

  await page.getByRole('button', { name: /add activity/i }).click()
  const form = page.getByRole('heading', { name: 'Log a session' })
  await expect(form).toBeVisible()
  await page.getByRole('textbox', { name: 'Day', exact: true }).fill(day)
  await page.getByRole('button', { name: /^run$/i }).click()
  await page.getByLabel('Duration (minutes)').fill('50')
  await page.getByRole('spinbutton', { name: /Distance/ }).fill('9.5')
  await page.getByRole('textbox', { name: /^Notes/ }).fill('easy loop by the river')
  await page.getByRole('button', { name: 'Save session' }).click()
  await expect(form).toHaveCount(0)

  await page.goto(`/workout/${day}`)
  // The plan never held this day, so the page does not say it dropped out.
  await expect(page.getByText('Your plan does not cover this day.')).toBeVisible()
  await expect(page.getByText(/no longer part of your current plan/i)).toHaveCount(0)

  const card = page.locator('section', { hasText: 'What you did this day' })
  await expect(card.getByText('Entered by you')).toBeVisible()
  await expect(card.getByText('Distance: 9.5 km')).toBeVisible()
  await expect(card.getByText('easy loop by the river')).toBeVisible()
  await expect(card.getByText(/^Effort: \d\/5/)).toBeVisible()

  await card.getByRole('button', { name: /edit the running you entered/i }).click()
  await expect(page.getByRole('heading', { name: 'Edit activity' })).toBeVisible()
  await page.getByLabel('Duration (minutes)').fill('65')
  await page.getByRole('textbox', { name: /^Notes/ }).fill('river loop, then hills')
  await page.getByRole('button', { name: 'Save changes' }).click()
  await expect(page.getByRole('heading', { name: 'Edit activity' })).toHaveCount(0)

  await expect(card.getByText('river loop, then hills')).toBeVisible()
  await expect(card.getByText('1h 5m')).toBeVisible()

  // Saved on the server, not only on this page.
  await page.reload()
  await expect(card.getByText('river loop, then hills')).toBeVisible()

  expect(errors, `console errors: ${errors.join(' | ')}`).toEqual([])
})
