/**
 * Settings in five sections, with expert controls hidden (ai-trainer-ops#50).
 *
 * The jsdom tests in `SettingsPage.test.tsx` prove the structure. This spec
 * adds what only a browser shows: that the page does not scroll sideways on a
 * phone, and that the section nav really scrolls to its section. It also
 * attaches a full-page screenshot per project (390 and 1366 px) to the
 * Playwright report artifact, which the issue asks for. Last, it pins what
 * Coach & AI and Your data show with Expert mode off and on (ai-trainer-ops#217).
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

  // What each mode shows (ai-trainer-ops#217). Without Expert mode, Coach & AI
  // keeps the switch to stop learning and the folded key, and the memory's
  // export and clear sit under Your data.
  const coach = page.locator('#settings-coach')
  const data = page.locator('#settings-data')
  await expect(coach.getByRole('switch')).toBeVisible()
  await expect(coach.getByText('Own key (optional)')).toBeVisible()
  await expect(coach.getByText('OpenAI')).toHaveCount(0)
  await expect(coach.getByRole('heading', { name: 'What You Train For' })).toHaveCount(0)
  await expect(coach.getByRole('button', { name: 'Export my memory' })).toHaveCount(0)
  await expect(data.getByRole('button', { name: 'Export my memory' })).toBeVisible()
  await expect(data.getByRole('button', { name: 'Clear all memory' })).toBeVisible()

  // Expert mode brings back the providers and what the athlete trains for.
  await page.getByRole('button', { name: /^(Expert|Toggle expert mode)$/ }).click()
  await expect(coach.getByRole('heading', { name: 'What You Train For' })).toBeVisible()
  await expect(coach.getByRole('button', { name: /^OpenAI/ }).first()).toBeVisible()
  await expect(data.getByRole('button', { name: 'Export my memory' })).toBeVisible()
})
