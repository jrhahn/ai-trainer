/**
 * Every button a thumb has to hit is at least 40 × 40 px on a phone
 * (ai-trainer-ops#270): the coach page with its chat, the day page and Settings.
 * Buttons were 34 px, chips 30 px, text buttons as high as their text.
 */

import { expect, test, type Page } from '@playwright/test'

function newAthlete() {
  const stamp = `${Date.now()}-${Math.floor(Math.random() * 1e6)}`
  return {
    name: 'Tia Touch',
    email: `e2e-touch-${stamp}@aitrainer-e2e.com`,
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
  // Health data need explicit consent (ai-trainer-ops#2).
  await page.getByRole('checkbox', { name: /privacy policy/i }).check()
  await page.getByRole('button', { name: 'Create Account' }).click()
  await expect(page.getByText(`Welcome, ${athlete.name}!`).first()).toBeVisible({
    timeout: 30_000,
  })
  for (let step = 0; step < 4; step++) {
    // Step 4 opens on intervals.icu for a new account (ai-trainer-ops#213).
    if (step === 3) await page.getByRole('button', { name: 'enter sessions myself', exact: false }).click()
    await page.getByRole('button', { name: /^continue$/i }).first().click()
  }
  await page.getByRole('button', { name: /generate my 14-day training plan/i }).click()
  await expect(
    page.getByRole('heading', { name: new RegExp(athlete.name.split(' ')[0], 'i') }).first()
  ).toBeVisible({ timeout: 60_000 })
}

function today() {
  const date = new Date()
  return [date.getFullYear(), String(date.getMonth() + 1).padStart(2, '0'), String(date.getDate()).padStart(2, '0')].join('-')
}

/** The visible buttons smaller than 40 × 40 px, named so a failure says which. */
async function smallTargets(page: Page) {
  return page.evaluate(() =>
    Array.from(document.querySelectorAll<HTMLElement>('button, [role="button"]'))
      .map((element) => ({ element, box: element.getBoundingClientRect() }))
      .filter(({ element, box }) => box.width > 0 && box.height > 0 && getComputedStyle(element).visibility !== 'hidden')
      .filter(({ box }) => box.width < 40 || box.height < 40)
      .map(({ element, box }) => ({
        name: (element.getAttribute('aria-label') || element.textContent || '').trim().slice(0, 40),
        width: Math.round(box.width),
        height: Math.round(box.height),
      }))
  )
}

test('every button on the coach page, the day page and Settings is a 40 px target', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'mobile-390', 'the phone width is what this is about')
  await onboard(page, newAthlete())

  for (const path of ['/', `/workout/${today()}`, '/settings']) {
    await page.goto(path)
    await page.waitForLoadState('networkidle')
    expect(await smallTargets(page), path).toEqual([])
  }
})
