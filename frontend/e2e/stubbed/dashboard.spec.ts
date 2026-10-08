/**
 * Onboarding finishes and the athlete lands on a dashboard with a plan
 * (ai-trainer-ops#46).
 *
 * The half of the stranger's journey that could not be tested until there was a
 * stub provider. Everything past "Generate My 14-Day Training Plan" needs plan
 * generation to *succeed*, which needs a model — and CI cannot carry a provider
 * key. The keyless stack next door asserts the opposite case, that a missing key
 * is named rather than swallowed.
 *
 * What is asserted here is the shape of the journey, never the content of the
 * plan. The plan is `[stub]` placeholder text by construction, so a test reading
 * it would be reading `llm._stub_plan_days` and would fail the day someone
 * improves the stub. The questions worth asking are: did the account become
 * onboarded, is there a session on the dashboard, and did the athlete's own data
 * survive the trip.
 */

import { expect, test, type ConsoleMessage, type Page } from '@playwright/test'

function newAthlete() {
  const stamp = `${Date.now()}-${Math.floor(Math.random() * 1e6)}`
  return {
    name: 'Robin Stub',
    // Not `.invalid`: RFC 2606 reserves it and `email-validator` refuses
    // special-use domains, so a fixture using one cannot register at all.
    email: `e2e-stub-${stamp}@aitrainer-e2e.com`,
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

/** Register, walk onboarding to the end, and generate. */
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
}

/** Wait for a dashboard that actually has a plan on it.
 *
 * Two headings, and both are structure rather than content: the greeting only
 * renders on the dashboard and carries the athlete's own name, and the week
 * strip only has a section once there are days to put in it. Neither reads the
 * plan's text, which is `[stub]` by construction — a test asserting on that
 * would be asserting on `llm._stub_plan_days`.
 *
 * Matched case-insensitively on purpose: the headings are uppercased in CSS, so
 * the DOM says "Your week" while the screen says "YOUR WEEK", and an exact
 * matcher finds neither.
 */
async function waitForPopulatedDashboard(page: Page, athlete: ReturnType<typeof newAthlete>) {
  const firstName = athlete.name.split(' ')[0]
  await expect(
    page.getByRole('heading', { name: new RegExp(firstName, 'i') }).first()
  ).toBeVisible({ timeout: 60_000 })
  await expect(page.getByRole('heading', { name: /your week/i }).first()).toBeVisible()
}

test.describe('a stranger finishes onboarding', () => {
  test('lands on a dashboard with a session on it', async ({ page }) => {
    const errors = watchConsole(page)
    const athlete = newAthlete()

    await onboard(page, athlete)

    // Positively, not by absence. "No session planned for today" being missing
    // is also true of a blank page, a crash and a redirect — and the first
    // version of this spec asserted only absences, passed, and was measuring
    // nothing. The whole reason this stack exists is to see a *populated*
    // dashboard, so it has to assert one.
    await waitForPopulatedDashboard(page, athlete)

    // And the empty state is gone, which is the specific thing #41 left behind.
    await expect(page.getByText(/no session planned for today/i)).toHaveCount(0)

    expect(errors, `console errors: ${errors.join(' | ')}`).toEqual([])
  })

  test('still knows the athlete after a reload', async ({ page }) => {
    const errors = watchConsole(page)
    // The state the dead end used to leave behind was only visible after a
    // reload: server says onboarded, client has no plan. Now that onboarding
    // can finish, the same reload should show a finished account — and the name
    // that survived both writes.
    const athlete = newAthlete()

    await onboard(page, athlete)
    // The dashboard, not "the generate button went away". Waiting on the button
    // is a race and this test found it: the button hides while generation is
    // still in flight, so the reload fired before `isOnboarded` had been
    // written and landed back on step 5. I took that for a product bug for a
    // while; the app is fine, the signal was wrong.
    await waitForPopulatedDashboard(page, athlete)

    await page.reload()

    await waitForPopulatedDashboard(page, athlete)
    await expect(page.getByText(/no session planned for today/i)).toHaveCount(0)

    // After the reload especially: this is where the #41 failure surfaced, and
    // a rehydration that throws would otherwise show up only as a slower test.
    expect(errors, `console errors: ${errors.join(' | ')}`).toEqual([])
  })

  test('the dashboard does not scroll sideways', async ({ page }) => {
    // The defects in ai-trainer-ops#51 that nobody could measure, because they
    // are behind an onboarded account. The same assertion the public pages get,
    // now reachable.
    const athlete = newAthlete()

    await onboard(page, athlete)
    // Waiting for the dashboard itself, not for the button to vanish: the
    // measurement below is meaningless on a page that has not finished
    // rendering, and "the button is gone" is equally true of a crash.
    await waitForPopulatedDashboard(page, athlete)

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
      `dashboard overflows by ${scrollWidth - clientWidth}px; widest element: ` +
        `<${widest.tag} class="${widest.className}"> reaching ${widest.right}px`
    ).toBeLessThanOrEqual(clientWidth)
  })
})
