/**
 * The dashboard picture on the landing page (ai-trainer-ops#53 item 4, #81 Q19).
 *
 * A synthetic athlete with a realistic fortnight, never real data: the stub's
 * own plan is `[stub]` text by construction, so after onboarding this replaces
 * it through the API with sessions a reader would recognise. Every run attaches
 * the picture to the report, so CI always has a current one. With
 * `UPDATE_LANDING_SCREENSHOT=1` it also writes the copy the landing page serves,
 * `public/landing-dashboard.png`; that copy changes only when someone does so.
 */

import { fileURLToPath } from 'node:url'
import { expect, test, type Page } from '@playwright/test'
import { STUB_BACKEND_PORT } from '../../playwright.stub.config'

const API = `http://127.0.0.1:${STUB_BACKEND_PORT}/api/v1`
const SERVED_COPY = fileURLToPath(new URL('../../public/landing-dashboard.png', import.meta.url))

const athlete = {
  name: 'Sam Rider',
  email: `e2e-landing-${Date.now()}-${Math.floor(Math.random() * 1e6)}@aitrainer-e2e.com`,
  password: 'Str0ng!Passw0rd',
}

function isoDate(offset: number) {
  const date = new Date()
  date.setDate(date.getDate() + offset)
  return [
    date.getFullYear(),
    String(date.getMonth() + 1).padStart(2, '0'),
    String(date.getDate()).padStart(2, '0'),
  ].join('-')
}

// One week, repeated: a key session, easy days, a long ride at the weekend. The
// planner's own vocabulary, with the purpose and cues a generated day carries.
const WEEK = [
  { workoutType: 'intervals', title: 'Sweet spot 3 × 12 min', durationMinutes: 75,
    description: 'Three 12-minute blocks at 88–93 % of FTP with 5 minutes easy between.',
    workoutPurpose: 'Raises the power you can hold for an hour without a hard day’s cost.',
    keyFocusPoints: ['Even pacing across all three blocks', 'Cadence 85–95'] },
  { workoutType: 'recovery', title: 'Easy spin', durationMinutes: 45,
    description: 'Zone 1, high cadence. Legs should feel better at the end than at the start.',
    workoutPurpose: 'Blood flow without load, so Thursday is fresh.',
    keyFocusPoints: ['Keep it conversational'] },
  { workoutType: 'rest', title: 'Rest day', durationMinutes: 0, description: 'Off the bike.' },
  { workoutType: 'intervals', title: 'VO₂max 5 × 4 min', durationMinutes: 70,
    description: 'Five 4-minute efforts at 110–118 % of FTP, 4 minutes easy between.',
    workoutPurpose: 'Lifts the ceiling that limits your threshold.',
    keyFocusPoints: ['Start the first rep controlled', 'Full recovery between reps'] },
  { workoutType: 'endurance', title: 'Endurance ride', durationMinutes: 90,
    description: 'Steady zone 2. Keep the climbs seated.',
    workoutPurpose: 'Aerobic base at a cost you recover from overnight.',
    keyFocusPoints: ['Fuel every 30 minutes'] },
  { workoutType: 'endurance', title: 'Long ride with friends', durationMinutes: 180,
    description: 'Zone 2 with the group; let the last hour be a little harder if you feel good.',
    workoutPurpose: 'Durability: power late in a long day.',
    keyFocusPoints: ['Eat before you are hungry'] },
  { workoutType: 'rest', title: 'Rest day', durationMinutes: 0, description: 'Off the bike.' },
]

async function onboard(page: Page) {
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
    await page.getByRole('button', { name: /^continue$/i }).first().click()
  }
  await page.getByRole('button', { name: /generate my 14-day training plan/i }).click()
  await expect(page.getByRole('heading', { name: /your week/i }).first()).toBeVisible({
    timeout: 60_000,
  })
}

/** Replace the stub's fortnight with the realistic one, as the athlete. */
async function writeRealisticPlan(page: Page) {
  // Without `X-Auth-Mode: cookie` the login answers with a bearer token.
  const login = await page.request.post(`${API}/auth/login`, {
    data: { email: athlete.email, password: athlete.password },
  })
  expect(login.ok()).toBeTruthy()
  const { access_token: token } = await login.json()
  // From three days back, so the week strip shows days already done.
  const plan = Array.from({ length: 17 }, (_, index) => {
    const offset = index - 3
    // Today is the key session, so the picture leads with it.
    const day = WEEK[((offset % WEEK.length) + WEEK.length) % WEEK.length]
    return {
      date: isoDate(offset),
      sport: 'cycling',
      ...day,
      ...(offset < 0 && day.workoutType !== 'rest' ? { completed: true } : {}),
    }
  })
  const put = await page.request.put(`${API}/users/me/plan`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { plan },
  })
  expect(put.ok()).toBeTruthy()
}

test('the landing page picture is a real dashboard with a realistic week', async ({ page }, testInfo) => {
  // One picture, at the width the landing page shows it.
  test.skip(testInfo.project.name !== 'desktop-1366', 'the picture is taken at desktop width')
  await onboard(page)
  await writeRealisticPlan(page)

  await page.reload()
  await expect(page.getByText(WEEK[0].title).first()).toBeVisible({ timeout: 30_000 })
  // Visible text only: the picture shows nothing else. The coach timeline's
  // collapsed plan-change groups list the stub's own day titles from the
  // onboarding fortnight, and counting those made the test pass or fail on
  // whether the plan history had loaded yet.
  await expect(page.getByText('[stub]').filter({ visible: true })).toHaveCount(0)

  // Today's session and the week: what #53 asked the picture to show.
  const week = await page.getByRole('heading', { name: /your week/i }).first().boundingBox()
  const strip = week ? week.y + week.height + 130 : 680
  const clip = { x: 0, y: 0, width: 1366, height: Math.round(strip) }
  const shot = testInfo.outputPath('landing-dashboard.png')
  await page.screenshot({ path: shot, clip })
  await testInfo.attach('landing-dashboard', { path: shot, contentType: 'image/png' })
  if (process.env.UPDATE_LANDING_SCREENSHOT === '1') {
    await page.screenshot({ path: SERVED_COPY, clip })
  }
})
