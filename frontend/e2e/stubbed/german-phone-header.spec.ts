/**
 * A German phone: the dashboard header and the data-load banner do not overlap
 * (ai-trainer-ops#131).
 *
 * The banner sat at `top-0` with `z-50` over the 56 px mobile top bar, so the
 * menu button could not be reached until its English "Dismiss" was tapped. And
 * "Aktivität hinzufügen" left about 150 px of a 390 px screen for the greeting,
 * which then took three lines. Only a browser lays this out, so it is measured
 * here, and the screenshot goes into the Playwright report as the issue asks.
 */

import { expect, test, type Locator, type Page } from '@playwright/test'

function newAthlete() {
  const stamp = `${Date.now()}-${Math.floor(Math.random() * 1e6)}`
  return {
    name: 'Jürgen Kopfzeile',
    email: `e2e-header-${stamp}@aitrainer-e2e.com`,
    password: 'Str0ng!Passw0rd',
  }
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
  await expect(page.getByRole('heading', { name: /Jürgen/ }).first()).toBeVisible({ timeout: 60_000 })
}

async function box(locator: Locator) {
  const found = await locator.boundingBox()
  expect(found, 'element has no box').not.toBeNull()
  return found!
}

function overlaps(a: { x: number; y: number; width: number; height: number }, b: typeof a) {
  return a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height
}

test('a German phone shows the header and the banner without overlap', async ({ page }, testInfo) => {
  test.skip((page.viewportSize()?.width ?? 0) > 500, 'the phone layout only')

  await onboard(page, newAthlete())
  await page.evaluate(() => localStorage.setItem('ai_trainer_ui_language', 'de'))
  // One failed load raises the banner.
  await page.route('**/api/v1/users/me/race-events', (route) =>
    route.fulfill({ status: 500, contentType: 'application/json', body: '{"detail":"down"}' })
  )
  await page.reload()

  const banner = page.getByText(/Some data failed to load/)
  await expect(banner).toBeVisible({ timeout: 30_000 })
  const dismiss = page.getByRole('button', { name: 'Ausblenden' })
  await expect(dismiss).toBeVisible()

  const menu = page.getByRole('button', { name: 'Toggle menu' })
  const greeting = page.getByRole('heading', { level: 1 })
  const add = page.getByRole('button', { name: 'Aktivität hinzufügen' })

  const bannerBox = await box(banner.locator('xpath=ancestor::div[contains(@class,"fixed")][1]'))
  const menuBox = await box(menu)
  expect(overlaps(bannerBox, menuBox), 'the banner covers the menu button').toBe(false)

  // The greeting keeps to at most two lines beside the add button.
  const greetingBox = await box(greeting)
  const lineHeight = await greeting.evaluate((el) => parseFloat(getComputedStyle(el).lineHeight))
  expect(Math.round(greetingBox.height / lineHeight)).toBeLessThanOrEqual(2)
  expect(overlaps(greetingBox, await box(add))).toBe(false)

  // The menu still opens with the banner up.
  await menu.click()

  await testInfo.attach('german-phone-header', {
    body: await page.screenshot({ fullPage: false }),
    contentType: 'image/png',
  })
})
