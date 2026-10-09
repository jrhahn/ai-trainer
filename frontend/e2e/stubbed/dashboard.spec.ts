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

    // ai-trainer-ops#40, in the only place it is still catchable. Onboarding
    // used to copy `name` into its form at mount — before the profile had
    // arrived — and write the empty copy back, so the athlete registered as
    // "Robin Stub" and became "athlete". Only a name that comes back from the
    // server *after* `updateCurrentUser`, which is what this reload reads,
    // proves the field survived.
    //
    // Through the greeting, anchored, and not by looking for the full name:
    // "Robin Stub" is only on screen at desktop width, so the first version of
    // this assertion passed at 1366 px and failed at 390.
    //
    // Anchored because `waitForPopulatedDashboard` above already matches the
    // heading on "Robin" unanchored, which is enough to catch the overwrite as
    // the issue originally described it — and that exact mutation is no longer
    // reachable anyway: the backend now rejects a blank `name`, so writing one
    // fails validation rather than reaching the dashboard. Measured, when
    // trying to mutation-check this line.
    //
    // What the anchored form adds is the overwrite that *is* still reachable: a
    // different, valid name. Mutating the profile write to "Robinson Stubbs"
    // satisfies the helper's `/robin/i` and fails here —
    // `Good morning, Robinson!` against `Good morning, Robin!`.
    const greeting = await page
      .getByRole('heading', { level: 1 })
      .first()
      .innerText()
    expect(greeting, 'the registered name should survive the profile write').toMatch(
      new RegExp(`^Good (morning|afternoon|evening), ${athlete.name.split(' ')[0]}!$`)
    )

    // This assertion moved here from `e2e/keyless/stranger-path.spec.ts`, which
    // can no longer reach a profile write at all: in BYOK-only mode onboarding
    // stops at the key step (ai-trainer-ops#41).

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

  test('says "Coach Timeline" once where a sighted reader can see it', async ({ page }) => {
    // ai-trainer-ops#51.6. The section label and the card's own title carried
    // the same words a hand's width apart.
    //
    // Measured by area, not by Playwright's visibility: `sr-only` hides with
    // `clip`, leaving a 1x1 box, so `toBeVisible()` still says yes and
    // `innerText` still includes the text. Neither would have noticed the fix.
    // What a sighted reader experiences is whether the words occupy space.
    const athlete = newAthlete()
    await onboard(page, athlete)
    await waitForPopulatedDashboard(page, athlete)

    const visibleCount = await page.evaluate(() => {
      const wanted = 'coach timeline'
      let count = 0
      for (const element of Array.from(document.querySelectorAll('*'))) {
        // Leaf-ish only: every ancestor also "contains" the text.
        if (element.children.length > 0) continue
        if ((element.textContent || '').trim().toLowerCase() !== wanted) continue
        const box = element.getBoundingClientRect()
        if (box.width > 10 && box.height > 10) count += 1
      }
      return count
    })

    expect(visibleCount, 'the words should occupy space exactly once').toBe(1)

    // And the landmark survived, which is why it was hidden rather than deleted.
    await expect(page.getByRole('heading', { name: /coach timeline/i })).toHaveCount(1)
  })

  test('is never asked for an API key', async ({ page }) => {
    // The other half of ai-trainer-ops#41's conditional step. This stack needs
    // no key — the stub stands in for the model — so the step must not appear,
    // and the wizard must still be five steps long.
    //
    // Worth its own test rather than a line in the walk above: a step that is
    // *always* shown and a step that is *never* shown each satisfy exactly one
    // of this and its companion in `e2e/keyless/key-step.spec.ts`.
    const athlete = newAthlete()

    await page.goto('/register')
    await page.getByPlaceholder('Your name').fill(athlete.name)
    await page.getByPlaceholder('you@example.com').fill(athlete.email)
    await page.getByPlaceholder('Strong password').fill(athlete.password)
    await page.getByPlaceholder('Repeat your password').fill(athlete.password)
    await page.getByRole('button', { name: 'Create Account' }).click()
    await expect(page.getByText(`Welcome, ${athlete.name}!`).first()).toBeVisible({
      timeout: 30_000,
    })

    await expect(page.getByText('Step 1 of 5')).toBeVisible()

    for (let step = 0; step < 4; step++) {
      await expect(page.getByRole('heading', { name: /add your gemini key/i })).toHaveCount(0)
      await page.getByRole('button', { name: /^continue$/i }).first().click()
    }

    // Straight to the summary, where Generate does work here.
    await expect(page.getByText('Ready to Go!')).toBeVisible()
    await expect(page.getByRole('heading', { name: /add your gemini key/i })).toHaveCount(0)
    await expect(page.getByText('Step 5 of 5')).toBeVisible()
  })
})
