/**
 * Settings in five sections, with expert controls hidden (ai-trainer-ops#50).
 *
 * The jsdom tests in `SettingsPage.test.tsx` prove the structure. This spec is
 * here for the two screenshots the issue asks for, at 390 and 1366 px, which
 * land in the Playwright report artifact of every run.
 */

import { expect, test, type Page } from '@playwright/test'

function newAthlete() {
  const stamp = `${Date.now()}-${Math.floor(Math.random() * 1e6)}`
  return {
    name: 'Sam Settings',
    email: `e2e-settings-${stamp}@aitrainer-e2e.com`,
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

for (const viewport of [
  { width: 390, height: 844 },
  { width: 1366, height: 900 },
]) {
  test(`Settings has five sections and no expert controls at ${viewport.width} px`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize(viewport)
    await onboard(page, newAthlete())
    await page.goto('/settings')

    const nav = page.getByRole('navigation', { name: 'Settings sections' })
    await expect(nav.getByRole('link')).toHaveText([
      'Profile',
      'Activities',
      'Coach & AI',
      'Account & security',
      'Your data',
    ])
    await expect(page.getByRole('button', { name: /Recalculate/ })).toHaveCount(0)

    // The section nav takes the athlete to the section, not just the URL.
    await nav.getByRole('link', { name: 'Your data' }).click()
    await expect(page.getByRole('heading', { name: 'Your data', exact: true })).toBeInViewport()

    await page.evaluate(() => window.scrollTo(0, 0))
    await testInfo.attach(`settings-${viewport.width}px`, {
      body: await page.screenshot({ fullPage: true }),
      contentType: 'image/png',
    })
  })
}
