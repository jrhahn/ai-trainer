/**
 * Entering an activity no plan day holds (ai-trainer-ops#47), in the browser.
 *
 * The unit tests pin the dialog and the list with the network mocked; this is
 * the one place the whole loop runs — dialog, route, load chain, the list the
 * dashboard draws from it. It found the bug it now guards: two runs entered on
 * one day were one activity, merged twice over, once by the importer's fuzzy
 * match on the server and once by the dashboard's on the client.
 */

import { expect, test, type ConsoleMessage, type Page } from '@playwright/test'

function newAthlete() {
  const stamp = `${Date.now()}-${Math.floor(Math.random() * 1e6)}`
  return {
    name: 'Robin Entry',
    email: `e2e-entry-${stamp}@aitrainer-e2e.com`,
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

async function enterRun(page: Page, minutes: string) {
  await page.getByRole('button', { name: /add activity/i }).click()
  const dialog = page.getByRole('heading', { name: 'Add activity' })
  await expect(dialog).toBeVisible()
  await page.getByRole('button', { name: /^run$/i }).click()
  await page.getByLabel('Duration (minutes)').fill(minutes)
  await page.getByRole('button', { name: 'Save activity' }).click()
  await expect(dialog).toHaveCount(0)
}

test('two runs entered on one day are two activities, and one can go again', async ({ page }) => {
  const errors = watchConsole(page)
  await onboard(page, newAthlete())

  await enterRun(page, '45')
  await enterRun(page, '30')

  const entries = page.getByText('Entered by you')
  await expect(entries).toHaveCount(2)

  // A run entered on a ride day is not the ride. It used to tick the planned
  // session "Done" and ask "Recovery? Tempo? VO2max?" about itself.
  await expect(page.getByText('Done', { exact: true })).toHaveCount(0)
  await expect(page.getByText(/was Recovery/)).toHaveCount(0)

  page.once('dialog', (dialog) => void dialog.accept())
  await page.getByRole('button', { name: /delete the running you entered/i }).first().click()
  await expect(entries).toHaveCount(1)

  // And it is gone on the server too, not just from the list.
  await page.reload()
  await expect(page.getByText('Entered by you')).toHaveCount(1)

  expect(errors, `console errors: ${errors.join(' | ')}`).toEqual([])
})

test('a GPX export is refused with the way forward, inside the same dialog (ai-trainer-ops#48)', async ({ page }) => {
  const errors = watchConsole(page)
  await onboard(page, newAthlete())

  await page.getByRole('button', { name: /add activity/i }).click()
  await page.getByRole('tab', { name: 'Upload a file' }).click()
  await page.locator('input[type="file"]').setInputFiles({
    name: 'morning-ride.gpx',
    mimeType: 'application/gpx+xml',
    buffer: Buffer.from('<?xml version="1.0"?><gpx version="1.1"></gpx>'),
  })

  await expect(page.getByText('0 imported')).toBeVisible()
  await expect(page.getByText(/GPX and TCX exports don.t carry what the coach needs/)).toBeVisible()
  expect(errors, `console errors: ${errors.join(' | ')}`).toEqual([])
})

