/**
 * Every onboarding answer can be changed later (ai-trainer-ops#202).
 *
 * The welcome step promises it: "No worries if you are unsure, just talk to the
 * trainer later to adapt or change it in your settings." This walks through
 * the four answers in Settings, checks they are stored and that the plan is
 * rebuilt by itself (#206), and changes one more in the chat.
 */

import { expect, test, type ConsoleMessage, type Page } from '@playwright/test'
import { STUB_BACKEND_PORT } from '../../playwright.stub.config'

const API = `http://127.0.0.1:${STUB_BACKEND_PORT}/api/v1`

function newAthlete() {
  const stamp = `${Date.now()}-${Math.floor(Math.random() * 1e6)}`
  return {
    name: 'Alex Answers',
    email: `e2e-answers-${stamp}@aitrainer-e2e.com`,
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

async function onboard(page: Page, athlete: ReturnType<typeof newAthlete>) {
  await page.goto('/register')
  await page.getByPlaceholder('Your name').fill(athlete.name)
  await page.getByPlaceholder('you@example.com').fill(athlete.email)
  await page.getByPlaceholder('Strong password').fill(athlete.password)
  await page.getByPlaceholder('Repeat your password').fill(athlete.password)
  await page.getByRole('checkbox', { name: /privacy policy/i }).check()
  await page.getByRole('button', { name: 'Create Account' }).click()
  await expect(page.getByText(`Welcome, ${athlete.name}!`).first()).toBeVisible({ timeout: 30_000 })
  for (let step = 0; step < 4; step++) {
    await page.getByRole('button', { name: /^continue$/i }).first().click()
  }
  await page.getByRole('button', { name: /generate my 14-day training plan/i }).click()
  await expect(page.getByRole('heading', { name: /your week/i }).first()).toBeVisible({
    timeout: 60_000,
  })
}

/** A local calendar date `days` from today, as the date input takes it. */
function daysAhead(days: number): string {
  const d = new Date()
  d.setDate(d.getDate() + days)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

test('the four onboarding answers change in Settings, and the level in the chat', async ({ page }) => {
  const errors = watchConsole(page)
  const athlete = newAthlete()
  await onboard(page, athlete)
  const race = daysAhead(90)

  await page.goto('/settings')
  const card = page.getByRole('region', { name: 'Your training' })
  const saved = card.getByText('Saved')

  // 1. Sports
  await card.getByRole('button', { name: 'Running' }).click()
  await expect(card.getByRole('button', { name: 'Running' })).toHaveAttribute('aria-pressed', 'true')
  await expect(saved.first()).toBeVisible()

  // 2. Race: a date, then a description
  await card.getByRole('button', { name: 'Training for a race' }).click()
  await card.getByLabel('Race Date').fill(race)
  await card.getByLabel('Race Description').fill('Gran fondo, 120 km')
  await card.getByLabel('Race Description').blur()

  // 3. Training days: Monday on, Sunday off
  await card.getByRole('button', { name: 'Mon', exact: true }).click()
  await expect(card.getByRole('button', { name: 'Mon', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await card.getByRole('button', { name: 'Sun', exact: true }).click()
  await expect(card.getByRole('button', { name: 'Sun', exact: true })).toHaveAttribute('aria-pressed', 'false')

  // The level
  await card.getByRole('button', { name: 'Advanced' }).click()
  await expect(card.getByRole('button', { name: 'Advanced' })).toHaveAttribute('aria-pressed', 'true')

  // Stored, not only shown.
  await page.reload()
  const after = page.getByRole('region', { name: 'Your training' })
  await expect(after.getByRole('button', { name: 'Running' })).toHaveAttribute('aria-pressed', 'true')
  await expect(after.getByRole('button', { name: 'Mon', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expect(after.getByRole('button', { name: 'Sun', exact: true })).toHaveAttribute('aria-pressed', 'false')
  await expect(after.getByLabel('Race Date')).toHaveValue(race)
  await expect(after.getByLabel('Race Description')).toHaveValue('Gran fondo, 120 km')
  await expect(after.getByRole('button', { name: 'Advanced' })).toHaveAttribute('aria-pressed', 'true')

  // The plan follows by itself (ai-trainer-ops#206), and the dashboard says
  // why. The changes may land in more than one rebuild; the level, changed
  // last, is in the last one. Waited for on the server first: a rebuild still
  // on its way when the page changes finishes there, and the dashboard would
  // ask before it is recorded.
  const login = await page.request.post(`${API}/auth/login`, {
    data: { email: athlete.email, password: athlete.password },
  })
  const { access_token: token } = await login.json()
  await expect
    .poll(
      async () => {
        const res = await page.request.get(`${API}/ai/plan-inputs-changed`, {
          headers: { Authorization: `Bearer ${token}` },
        })
        const body = await res.json()
        return !body.changed && (body.lastRebuild?.reasons ?? []).includes('level')
      },
      { timeout: 45_000 },
    )
    .toBe(true)
  await page.goto('/')
  const news = page.getByText(/^Your plan is rebuilt: /)
  await expect(news).toBeVisible({ timeout: 30_000 })
  await expect(news).toContainText('your level changed')
  await expect(page.getByRole('button', { name: /rebuild my plan/i })).toHaveCount(0)
  await page.getByRole('button', { name: 'Got it' }).click()
  await expect(news).toHaveCount(0)

  // And in the chat: the level said about oneself is offered, then set.
  await page.getByPlaceholder('Ask your coach...').fill("Honestly, I'm a beginner.")
  await page.getByRole('button', { name: 'Send message' }).click()
  await expect(page.getByText(/Update your profile:/)).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText('level: Beginner')).toBeVisible()
  await page.getByRole('button', { name: 'Update profile' }).click()
  await expect(page.getByText(/Updated\. The coach plans with it from now on\./)).toBeVisible()
  // Accepting the offer rebuilds the plan without a button (ai-trainer-ops#206).
  await expect(page.getByText(/^Your plan is rebuilt: your level changed/)).toBeVisible({ timeout: 30_000 })

  await page.goto('/settings')
  await expect(
    page.getByRole('region', { name: 'Your training' })
      .getByRole('button', { name: 'Beginner' }),
  ).toHaveAttribute('aria-pressed', 'true')

  expect(errors, `console errors: ${errors.join(' | ')}`).toEqual([])
})
