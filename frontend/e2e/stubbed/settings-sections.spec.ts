/**
 * Settings in five sections, with expert controls hidden (ai-trainer-ops#50).
 *
 * The jsdom tests in `SettingsPage.test.tsx` prove the structure. This spec
 * adds what only a browser shows: that the page does not scroll sideways on a
 * phone, and that the section nav really scrolls to its section. It also
 * attaches a full-page screenshot per project (390 and 1366 px) to the
 * Playwright report artifact, which the issue asks for.
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

test('Settings has five sections, no expert controls, and no sideways scroll', async ({
  page,
}, testInfo) => {
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

  // Same measurement as keyless/layout.spec.ts, naming the culprit on failure.
  const { scrollWidth, clientWidth, widest } = await page.evaluate(() => {
    let widest = { tag: '', className: '', right: 0 }
    for (const element of Array.from(document.querySelectorAll('*'))) {
      const box = element.getBoundingClientRect()
      if (box.right > widest.right) {
        widest = {
          tag: element.tagName,
          className: String((element as HTMLElement).className || '').slice(0, 80),
          right: Math.round(box.right),
        }
      }
    }
    return {
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
      widest,
    }
  })
  expect(
    scrollWidth,
    `/settings overflows by ${scrollWidth - clientWidth}px; widest element: ` +
      `<${widest.tag} class="${widest.className}"> reaching ${widest.right}px`
  ).toBeLessThanOrEqual(clientWidth)

  await testInfo.attach(`settings-${testInfo.project.name}`, {
    body: await page.screenshot({ fullPage: true }),
    contentType: 'image/png',
  })

  // The section nav takes the athlete to the section, not just the URL.
  await nav.getByRole('link', { name: 'Your data' }).click()
  await expect(page.getByRole('heading', { name: 'Your data', exact: true })).toBeInViewport()
})
