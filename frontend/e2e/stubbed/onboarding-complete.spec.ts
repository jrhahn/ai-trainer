/**
 * Onboarding with every optional field filled in, and what survives it
 * (ai-trainer-ops#46).
 *
 * The existing stubbed specs walk onboarding by clicking Continue four times,
 * which is the stranger who answers nothing. That path proves the journey works
 * and proves nothing about the answers: every field could be discarded on the
 * way and all of those specs would still pass.
 *
 * This one types into every input onboarding offers — race goal and date, a
 * fitness level, FTP, max heart rate, the structured-plan checkbox, an
 * assessment method — and reads back everything the app shows anywhere:
 *
 * | answer | read back from |
 * |---|---|
 * | name, email | Settings |
 * | FTP, max heart rate | Settings |
 * | race date, race description | the dashboard's season countdown |
 * | fitness level, assessment source, race goal | **not read back** — see below |
 * | structured-plan checkbox | **not read back** |
 *
 * The last two rows are the honest limit of this spec, and it was a review on
 * PR #797 that made me state it rather than imply otherwise: nothing in the app
 * displays `fitnessLevel`, `followsTrainingPlan` or `assessmentMethod`, so if
 * onboarding dropped them on the floor nothing here would notice. What is done
 * instead is to make the *entering* of them verifiable — every choice asserts
 * its own `aria-pressed` immediately after the click, so a locator that drifted
 * onto the wrong control fails here rather than passing quietly. Reading them
 * back needs somewhere to read them from, which is ai-trainer-ops#50's and
 * #43's territory.
 *
 * As everywhere in this stack, nothing reads the generated plan: it is `[stub]`
 * text by construction. What is read back is the athlete's own input.
 */

import { expect, test, type ConsoleMessage, type Page } from '@playwright/test'

/** What the athlete types. Distinctive values, so a default cannot pass for one. */
const ANSWERS = {
  // Not round numbers an input might coincidentally hold, and not the
  // placeholders (250 / 185) — a field that ignored the typing and showed its
  // own placeholder would otherwise read as a pass.
  ftp: '237',
  maxHeartRate: '191',
  raceDescription: 'Night-shift gran fondo, 2100 m of climbing',
}

function newAthlete() {
  const stamp = `${Date.now()}-${Math.floor(Math.random() * 1e6)}`
  return {
    name: 'Mika Thorough',
    // Not `.invalid`: RFC 2606 reserves it and `email-validator` refuses
    // special-use domains, so a fixture using one cannot register at all.
    email: `e2e-full-${stamp}@aitrainer-e2e.com`,
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

/** A date far enough out that the plan horizon cannot swallow it. */
function raceDate(): string {
  const day = new Date()
  day.setDate(day.getDate() + 90)
  return day.toISOString().slice(0, 10)
}

async function register(page: Page, athlete: ReturnType<typeof newAthlete>) {
  await page.goto('/register')
  await page.getByPlaceholder('Your name').fill(athlete.name)
  await page.getByPlaceholder('you@example.com').fill(athlete.email)
  await page.getByPlaceholder('Strong password').fill(athlete.password)
  await page.getByPlaceholder('Repeat your password').fill(athlete.password)
  await page.getByRole('button', { name: 'Create Account' }).click()
  await expect(page.getByText(`Welcome, ${athlete.name}!`).first()).toBeVisible({
    timeout: 30_000,
  })
}

const continueButton = (page: Page) => page.getByRole('button', { name: /^continue$/i }).first()

/** Walk onboarding answering everything it offers. */
async function onboardThoroughly(page: Page, athlete: ReturnType<typeof newAthlete>) {
  await register(page, athlete)

  // Step 1 is the greeting.
  await continueButton(page).click()

  // Plain strings rather than alternation regexes, and no `.first()`. The name
  // of each of these buttons is its whole card — label plus description — so the
  // match is a substring, and a substring that hit two cards used to be resolved
  // silently by taking the first. Playwright's strict mode now turns that into a
  // failure instead, which is the only way this step can tell us it has drifted
  // onto the wrong control. Each choice then asserts its own pressed state, so a
  // click that landed somewhere unexpected fails here rather than three steps
  // later (both points from reviews on PR #797).
  const choose = async (name: string) => {
    const option = page.getByRole('button', { name, exact: false })
    await option.click()
    await expect(option).toHaveAttribute('aria-pressed', 'true')
  }

  // Step 2 — the race goal, which is the branch that reveals the date fields.
  await choose('Race Prep')
  const date = page.locator('input[type="date"]')
  await expect(date).toBeVisible()
  await date.fill(raceDate())
  await page.getByPlaceholder(/gran fondo/i).fill(ANSWERS.raceDescription)
  await continueButton(page).click()

  // Step 3 — fitness level plus the two numbers the coach would otherwise have
  // to estimate from the first rides.
  await choose('Intermediate')
  await page.getByPlaceholder('e.g. 250').fill(ANSWERS.ftp)
  await page.getByPlaceholder('e.g. 185').fill(ANSWERS.maxHeartRate)
  // By name, not by position: the last unscoped locator in this spec. It is
  // wrapped in its own label, so it has one.
  const followsPlan = page.getByRole('checkbox', {
    name: /structured training plan/i,
  })
  await followsPlan.check()
  await expect(followsPlan).toBeChecked()
  await continueButton(page).click()

  // Step 4 — the assessment source. "Use parameters I entered" is the one that
  // needs no provider round trip, which is also what #43 calls out as two steps
  // for one decision.
  await choose('Use parameters I entered')
  await continueButton(page).click()

  await page.getByRole('button', { name: /generate my 14-day training plan/i }).click()
}

/** Open Settings.
 *
 * This walked through the nav, with a `TODO(ai-trainer-ops#57)` on it, because a
 * full load of any non-root route used to bounce to the dashboard — a defect
 * this spec found and #798 fixed. The workaround is gone with the bug: a plain
 * navigation is what an athlete's bookmark does, and `deep-link.spec.ts` is what
 * proves it works.
 */
async function openSettings(page: Page) {
  await page.goto('/settings')
  await expect(page.getByLabel('Display Name')).toBeVisible({ timeout: 30_000 })
}

async function waitForPopulatedDashboard(page: Page, athlete: ReturnType<typeof newAthlete>) {
  const firstName = athlete.name.split(' ')[0]
  await expect(
    page.getByRole('heading', { name: new RegExp(firstName, 'i') }).first()
  ).toBeVisible({ timeout: 60_000 })
  await expect(page.getByRole('heading', { name: /your week/i }).first()).toBeVisible()
}

test.describe('onboarding with everything filled in', () => {
  test('keeps the numbers the athlete typed, and shows who they are signed in as', async ({
    page,
  }) => {
    const errors = watchConsole(page)
    const athlete = newAthlete()

    await onboardThoroughly(page, athlete)
    await waitForPopulatedDashboard(page, athlete)

    // The race answers, read back where the app actually shows them: the season
    // countdown renders the description up to a spaced dash, and a day count
    // derived from the date. Both only appear if the two fields survived the
    // trip, which is what makes this worth asserting rather than the goal chip.
    await expect(page.getByText(ANSWERS.raceDescription).first()).toBeVisible()
    // 89–91 rather than exactly 90: the fixture builds the date from
    // `toISOString()` (UTC) while the countdown counts calendar days locally.
    await expect(page.getByText(/\b(89|90|91) days\b/).first()).toBeVisible()

    await openSettings(page)

    // Name and email, the pair ai-trainer-ops#46 asks for and #40 broke: the
    // summary step used to show them empty, and onboarding used to clear the
    // name outright.
    await expect(page.getByLabel('Display Name')).toHaveValue(athlete.name)
    await expect(page.getByText(`Signed in as ${athlete.email}`)).toBeVisible()

    // And the two figures. These are the ones that change what the coach
    // prescribes, so a discarded FTP is not a cosmetic loss.
    await expect(page.getByLabel('Current FTP (watts)')).toHaveValue(ANSWERS.ftp)
    await expect(page.getByLabel('Max Heart Rate (bpm)')).toHaveValue(ANSWERS.maxHeartRate)

    expect(errors, `console errors: ${errors.join(' | ')}`).toEqual([])
  })

  test('survives a reload, because the answers are on the account and not in the tab', async ({
    page,
  }) => {
    // Watched here too: the rehydration path is where a console error is most
    // likely, and leaving it off one of two tests was an inconsistency the
    // review on PR #797 picked up.
    const errors = watchConsole(page)
    const athlete = newAthlete()

    await onboardThoroughly(page, athlete)
    await waitForPopulatedDashboard(page, athlete)
    await openSettings(page)
    await expect(page.getByLabel('Current FTP (watts)')).toHaveValue(ANSWERS.ftp)

    // A full browser reload, which throws away everything the tab held, and
    // **stays on Settings** — which is the point and was not true when this test
    // was written. It used to walk back through the dashboard, because a reload
    // of any non-root route bounced there; the test had quietly encoded the very
    // defect the spec beside it had found (ai-trainer-ops#57, fixed in #798).
    // Asserting the page we are on is therefore part of the assertion, not
    // decoration.
    await page.reload()

    await expect(page.getByLabel('Current FTP (watts)')).toHaveValue(ANSWERS.ftp, {
      timeout: 30_000,
    })
    expect(new URL(page.url()).pathname).toBe('/settings')
    await expect(page.getByLabel('Max Heart Rate (bpm)')).toHaveValue(ANSWERS.maxHeartRate)
    await expect(page.getByText(`Signed in as ${athlete.email}`)).toBeVisible()

    expect(errors, `console errors: ${errors.join(' | ')}`).toEqual([])
  })
})
