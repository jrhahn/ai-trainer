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

  // Exact option labels rather than alternation regexes, and every choice
  // asserts that it took. A `/race/i`-style locator can drift onto a different
  // control when copy changes and the test still passes, which is the one way
  // these steps could lie (found in review on PR #797).
  const choose = async (name: string) => {
    const option = page.getByRole('button', { name, exact: false })
    await option.first().click()
    await expect(option.first()).toHaveAttribute('aria-pressed', 'true')
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
  const followsPlan = page.getByRole('checkbox').first()
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

/** Walk to Settings the way the athlete does, through the nav.
 *
 * Deliberately *not* `page.goto('/settings')`. A full load of any non-root
 * route bounces to the dashboard: `App.tsx` decides the route tree from
 * `authToken`, which is empty on the first render because the persisted store
 * rehydrates asynchronously, so `path="*"` fires `<Navigate to="/" replace>` and
 * replaces the URL before the token arrives. Bookmarking `/settings` or
 * `/workout/:date` therefore cannot work. That is a real defect and this spec
 * found it — it is written up separately rather than asserted here, because this
 * spec is about whether onboarding's answers reach the account, and a spec that
 * fails for a second reason tells you about neither.
 *
 * TODO(ai-trainer-ops#57): once the deep-link fix is in, this can go back to a
 * plain `page.goto('/settings')`. Left as a marker so the workaround does not
 * outlive the bug it works around.
 */
async function openSettings(page: Page) {
  const link = page.getByRole('link', { name: 'Settings' }).first()
  // Below 768px the sidebar is `hidden md:flex` and the nav lives in a drawer
  // that is only in the DOM while it is open, so the same walk needs one more
  // tap at 390px than at 1366px. Asking whether the link is there is what keeps
  // one helper working at both widths.
  if (!(await link.isVisible().catch(() => false))) {
    await page.getByRole('button', { name: 'Toggle menu' }).click()
  }
  await link.click()
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

    // A full browser reload, which throws away everything the tab held. The
    // reload lands on the dashboard rather than back on Settings — see
    // `openSettings` — so the walk is repeated, and what is being asserted is
    // that the figures come back from the account and not from the page.
    await page.reload()
    await waitForPopulatedDashboard(page, athlete)
    await openSettings(page)

    await expect(page.getByLabel('Current FTP (watts)')).toHaveValue(ANSWERS.ftp)
    await expect(page.getByLabel('Max Heart Rate (bpm)')).toHaveValue(ANSWERS.maxHeartRate)
    await expect(page.getByText(`Signed in as ${athlete.email}`)).toBeVisible()

    expect(errors, `console errors: ${errors.join(' | ')}`).toEqual([])
  })
})
