/**
 * The dashboard picture on the landing page (ai-trainer-ops#53 item 4, #81 Q19).
 *
 * A synthetic athlete with a realistic fortnight, never real data: the stub's
 * own plan is `[stub]` text by construction, so after onboarding this replaces
 * it through the API with sessions a reader would recognise. Eight synthetic
 * rides go in first, through the API, so the picture shows a rider with
 * history rather than "0 of 8 activities" (ai-trainer-ops#294). Every run attaches
 * the picture to the report, so CI always has a current one. With
 * `UPDATE_LANDING_SCREENSHOT=1` it also writes the copy the landing page serves,
 * `public/landing-dashboard.png`; that copy changes only when someone does so.
 */

import { fileURLToPath } from 'node:url'
import { expect, test, type Page } from '@playwright/test'
import { STUB_BACKEND_PORT } from '../../playwright.stub.config'

const API = `http://127.0.0.1:${STUB_BACKEND_PORT}/api/v1`
/** The served copy of one picture: `landing-dashboard[-phone][-de].png`. */
function servedCopy(name: string) {
  return fileURLToPath(new URL(`../../public/${name}.png`, import.meta.url))
}

const athlete = {
  name: 'Sam Rider',
  email: '',
  password: 'Str0ng!Passw0rd',
}

function freshAthlete() {
  athlete.email = `e2e-landing-${Date.now()}-${Math.floor(Math.random() * 1e6)}@aitrainer-e2e.com`
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

// The plan's texts as the German interface shows them. The stub model
// translates nothing, so the picture's German comes from here, the way
// german-journey.spec.ts does it (review of 2026-11-01, ai-trainer-ops#338 A5).
const GERMAN: Record<string, string> = {
  'Sweet spot 3 × 12 min': 'Sweet Spot 3 × 12 min',
  'Three 12-minute blocks at 88–93 % of FTP with 5 minutes easy between.':
    'Drei 12-Minuten-Blöcke bei 88–93 % der FTP, dazwischen 5 Minuten locker.',
  'Raises the power you can hold for an hour without a hard day’s cost.':
    'Hebt die Leistung, die du eine Stunde halten kannst, ohne die Kosten eines harten Tages.',
  'Even pacing across all three blocks': 'Gleichmäßig über alle drei Blöcke',
  'Cadence 85–95': 'Trittfrequenz 85–95',
  'Easy spin': 'Lockeres Rollen',
  'Zone 1, high cadence. Legs should feel better at the end than at the start.':
    'Zone 1, hohe Trittfrequenz. Die Beine sollen sich am Ende besser anfühlen als am Anfang.',
  'Blood flow without load, so Thursday is fresh.': 'Durchblutung ohne Belastung, damit du Donnerstag frisch bist.',
  'Keep it conversational': 'Im Plauderton',
  'Rest day': 'Ruhetag',
  'Off the bike.': 'Kein Rad heute.',
  'VO₂max 5 × 4 min': 'VO₂max 5 × 4 min',
  'Five 4-minute efforts at 110–118 % of FTP, 4 minutes easy between.':
    'Fünf 4-Minuten-Intervalle bei 110–118 % der FTP, dazwischen 4 Minuten locker.',
  'Lifts the ceiling that limits your threshold.': 'Hebt die Decke, die deine Schwelle begrenzt.',
  'Start the first rep controlled': 'Das erste Intervall kontrolliert beginnen',
  'Full recovery between reps': 'Zwischen den Intervallen ganz erholen',
  'Endurance ride': 'Grundlagenausfahrt',
  'Steady zone 2. Keep the climbs seated.': 'Gleichmäßig Zone 2. Anstiege im Sitzen.',
  'Aerobic base at a cost you recover from overnight.': 'Aerobe Grundlage zu Kosten, von denen du dich über Nacht erholst.',
  'Fuel every 30 minutes': 'Alle 30 Minuten essen',
  'Long ride with friends': 'Lange Runde mit Freunden',
  'Zone 2 with the group; let the last hour be a little harder if you feel good.':
    'Zone 2 mit der Gruppe; die letzte Stunde darf etwas härter sein, wenn du dich gut fühlst.',
  'Durability: power late in a long day.': 'Ausdauerfestigkeit: Leistung spät an einem langen Tag.',
  'Eat before you are hungry': 'Essen, bevor du Hunger hast',
}

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
    // Step 4 opens on intervals.icu for a new account (ai-trainer-ops#213).
    if (step === 3) await page.getByRole('button', { name: 'enter sessions myself', exact: false }).click()
    await page.getByRole('button', { name: /^continue$/i }).first().click()
  }
  await page.getByRole('button', { name: /generate my 14-day training plan/i }).click()
  await expect(page.getByRole('heading', { name: /your week/i }).first()).toBeVisible({
    timeout: 60_000,
  })
}

/** The athlete's bearer token, for the API calls below. */
async function login(page: Page): Promise<string> {
  // Without `X-Auth-Mode: cookie` the login answers with a bearer token.
  const response = await page.request.post(`${API}/auth/login`, {
    data: { email: athlete.email, password: athlete.password },
  })
  expect(response.ok()).toBeTruthy()
  return (await response.json()).access_token
}

// Two weeks of riding before the plan's first day: days back, minutes,
// perceived effort (1-5), km, average heart rate, normalized watts.
const RIDES: [number, number, number, number, number, number][] = [
  [-17, 60, 2, 28, 136, 178],
  [-16, 90, 3, 41, 139, 190],
  [-14, 75, 4, 34, 148, 214],
  [-13, 45, 1, 19, 124, 146],
  [-11, 120, 3, 55, 140, 186],
  [-9, 70, 4, 31, 151, 219],
  [-7, 60, 2, 27, 135, 175],
  [-5, 150, 3, 68, 141, 184],
]

/** Eight past rides, entered through the API as the athlete would by hand: the
 *  stub stack's analysis is stubbed and cannot price an import. Before the plan
 *  is written, so nothing it does to the plan survives. */
async function seedRides(page: Page, token: string) {
  for (const [offset, minutes, effort, km, heartRate, watts] of RIDES) {
    const response = await page.request.post(`${API}/users/me/activities`, {
      headers: { Authorization: `Bearer ${token}` },
      data: {
        date: isoDate(offset),
        sport: 'cycling',
        durationMinutes: minutes,
        perceivedEffort: effort,
        distanceKm: km,
        averageHeartRate: heartRate,
        normalizedPower: watts,
      },
    })
    expect(response.ok()).toBeTruthy()
  }
}

/** Replace the stub's fortnight with the realistic one, as the athlete. */
async function writeRealisticPlan(page: Page, token: string) {
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

// Four pictures: English and German, the desktop frame and the phone's
// (review of 2026-11-01, ai-trainer-ops#338 A5). The friends open the link on a
// phone in German, where the desktop picture was in English at about 3 px text.
for (const language of ['en', 'de'] as const) {
  test(`the landing page picture is a real dashboard with a realistic week (${language})`, async ({ page }, testInfo) => {
    const phone = testInfo.project.name === 'mobile-390'
    test.skip(!phone && testInfo.project.name !== 'desktop-1366', 'the pictures are taken at desktop and phone width')
    freshAthlete()
    await onboard(page)
    const token = await login(page)
    await seedRides(page, token)
    await writeRealisticPlan(page, token)

    if (language === 'de') {
      await page.evaluate(() => localStorage.setItem('ai_trainer_ui_language', 'de'))
      await page.route('**/api/v1/ai/plan-translations', async (route) => {
        if (route.request().method() !== 'POST') return route.continue()
        const response = await route.fetch()
        const answer = await response.json()
        await route.fulfill({
          response,
          json: { ...answer, translations: { ...answer.translations, ...GERMAN }, missing: false },
        })
      })
    }

    await page.reload()
    const title = language === 'de' ? GERMAN[WEEK[0].title] : WEEK[0].title
    await expect(page.getByText(title).first()).toBeVisible({ timeout: 30_000 })
    // Visible text only: the picture shows nothing else. The coach timeline's
    // collapsed plan-change groups list the stub's own day titles from the
    // onboarding fortnight, and counting those made the test pass or fail on
    // whether the plan history had loaded yet.
    await expect(page.getByText('[stub]').filter({ visible: true })).toHaveCount(0)

    // Today's session and the week: what #53 asked the picture to show. The season
    // card loads after the plan and pushes the week down; measured before it, the
    // frame cut the strip off (ai-trainer-ops#294). So the frame ends below the
    // week strip's own section, once the page has settled.
    await expect(page.getByText(language === 'de' ? /^Diese Woche:/ : /^This week:/).first()).toBeVisible({
      timeout: 30_000,
    })
    await page.waitForLoadState('networkidle')
    const strip = await page
      .getByRole('heading', { name: language === 'de' ? /deine woche/i : /your week/i })
      .first()
      .locator('xpath=ancestor::section[1]')
      .boundingBox()
    expect(strip).not.toBeNull()
    const width = phone ? 390 : 1366
    const clip = { x: 0, y: 0, width, height: Math.round(strip!.y + strip!.height + 24) }
    const name = `landing-dashboard${phone ? '-phone' : ''}${language === 'de' ? '-de' : ''}`
    const shot = testInfo.outputPath(`${name}.png`)
    await page.screenshot({ path: shot, clip })
    await testInfo.attach(name, { path: shot, contentType: 'image/png' })
    if (process.env.UPDATE_LANDING_SCREENSHOT === '1') {
      await page.screenshot({ path: servedCopy(name), clip })
    }
  })
}
