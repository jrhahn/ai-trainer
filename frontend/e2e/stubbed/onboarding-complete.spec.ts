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
 * assessment method — and then asks Settings what the account actually holds.
 * That is the assertion ai-trainer-ops#43 says nobody can make today ("some of
 * what it does ask has no visible effect"), and it is the one that would have
 * caught #40, where onboarding deleted the athlete's name.
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

  // Step 2 — the race goal, which is the branch that reveals the date fields.
  await page.getByRole('button', { name: /race/i }).first().click()
  const date = page.locator('input[type="date"]')
  await expect(date).toBeVisible()
  await date.fill(raceDate())
  await page.getByPlaceholder(/gran fondo/i).fill(ANSWERS.raceDescription)
  await continueButton(page).click()

  // Step 3 — fitness level plus the two numbers the coach would otherwise have
  // to estimate from the first rides.
  await page.getByRole('button', { name: /intermediate|1-3 years|1–3 years/i }).first().click()
  await page.getByPlaceholder('e.g. 250').fill(ANSWERS.ftp)
  await page.getByPlaceholder('e.g. 185').fill(ANSWERS.maxHeartRate)
  await page.getByRole('checkbox').first().check()
  await continueButton(page).click()

  // Step 4 — the assessment source. "Use the parameters I entered" is the one
  // that needs no provider round trip, which is also what #43 calls out as two
  // steps for one decision.
  await page.getByRole('button', { name: /parameters i entered|use my/i }).first().click()
  await continueButton(page).click()

  await page.getByRole('button', { name: /generate my 14-day training plan/i }).click()
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

    await page.goto('/settings')

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
    const athlete = newAthlete()

    await onboardThoroughly(page, athlete)
    await waitForPopulatedDashboard(page, athlete)
    await page.goto('/settings')
    await expect(page.getByLabel('Current FTP (watts)')).toHaveValue(ANSWERS.ftp)

    await page.reload()

    await expect(page.getByLabel('Current FTP (watts)')).toHaveValue(ANSWERS.ftp, {
      timeout: 30_000,
    })
    await expect(page.getByText(`Signed in as ${athlete.email}`)).toBeVisible()
  })
})
