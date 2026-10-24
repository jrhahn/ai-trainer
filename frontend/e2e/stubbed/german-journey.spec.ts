/**
 * A German account meets no English on its way in (ai-trainer-ops#121).
 *
 * The issue's "Done when": register → onboarding → first plan → chat → a 402
 * and a 502 in chat, in German, checked by one spec. The browser speaks German
 * (`locale: de-DE`), so the interface picks German on its own, the way a
 * friend's phone would.
 *
 * What it can and cannot claim. The stub's plan and coach text are `[stub]`
 * English by construction, and plan text stays English by design until the
 * owner decides otherwise (see the issue). So the spec does not fail on any
 * English word; it fails on the interface's own English, the strings the
 * issue found and this work translated, and it asserts their German.
 */

import { expect, test, type Page } from '@playwright/test'

test.use({ locale: 'de-DE' })

/** Interface strings that were English before #121; none may show. */
const FORMER_ENGLISH = [
  'Loading your training data',
  'Syncing profile, plan, workouts',
  'Sorry, I could not answer',
  'Settings → Your AI Provider Key',
  'You have used the free coaching allowance',
  'The AI model did not answer just now',
  'Some data failed to load',
  'Dismiss',
  // The onboarding greeting for this athlete; a bare "Welcome, " could match stub text.
  'Welcome, Jürgen',
  // The coach's fixed starting assumptions (ai-trainer-ops#215).
  'Has ridden',
  'Has trained to a structured plan',
  'Starting assumption from the onboarding answers',
]

function newAthlete() {
  const stamp = `${Date.now()}-${Math.floor(Math.random() * 1e6)}`
  return {
    name: 'Jürgen Deutsch',
    email: `e2e-german-${stamp}@aitrainer-e2e.com`,
    password: 'Str0ng!Passw0rd',
  }
}

async function noFormerEnglish(page: Page, where: string) {
  const text = await page.evaluate(() => document.body.innerText)
  for (const english of FORMER_ENGLISH) {
    expect(text, `"${english}" on ${where}`).not.toContain(english)
  }
}

test('a German account goes from sign-up to a chat error without English', async ({ page }) => {
  test.skip((page.viewportSize()?.width ?? 0) < 500, 'the language is the same on a phone; once is enough')

  const athlete = newAthlete()
  await page.goto('/register')
  // The example chat beside the form is German too.
  await expect(page.getByText('Dein Coach', { exact: true })).toBeVisible()
  await noFormerEnglish(page, '/register')

  await page.getByPlaceholder('Dein Name').fill(athlete.name)
  await page.getByPlaceholder('you@example.com').fill(athlete.email)
  await page.getByPlaceholder('Sicheres Passwort').fill(athlete.password)
  await page.getByPlaceholder('Passwort wiederholen').fill(athlete.password)
  // Health data need explicit consent (ai-trainer-ops#2).
  await page.getByRole('checkbox', { name: /Datenschutzerklärung/ }).check()
  await page.getByRole('button', { name: 'Konto erstellen' }).click()

  await expect(page.getByText(`Willkommen, ${athlete.name}!`).first()).toBeVisible({ timeout: 30_000 })
  await noFormerEnglish(page, 'onboarding')
  // Click on while there is a next step, so an added step does not end in a
  // confusing timeout on the final button.
  const next = page.getByRole('button', { name: /^weiter$/i }).first()
  for (let step = 0; step < 10 && (await next.isVisible()); step++) {
    // Step 4 opens on intervals.icu for a new account (ai-trainer-ops#213).
    if (step === 3) await page.getByRole('button', { name: 'Ich trage meine Einheiten selbst ein', exact: false }).click()
    await next.click()
  }
  await page.getByRole('button', { name: 'Meinen 14-Tage-Trainingsplan erstellen' }).click()
  await expect(page.getByRole('heading', { name: /Jürgen/ }).first()).toBeVisible({ timeout: 60_000 })
  // The coach speaks first, in German, without a reload (ai-trainer-ops#153).
  // The stub model answers nothing, so this is the fixed text.
  await expect(page.getByText('Hallo! Dein erster Plan steht.', { exact: false })).toBeVisible()
  // Its starting assumptions are pinned as questions, answered with one tap
  // (ai-trainer-ops#154, #155); the next one takes the pin.
  await expect(page.getByRole('group', { name: 'Antworten zum Antippen' })).toBeVisible()
  // The assumptions themselves, on the coach timeline, read German too
  // (ai-trainer-ops#215): they are stored in English for the engine.
  await expect(page.getByText('Du bist in den letzten zwei Monaten 3 bis 6 Stunden pro Woche gefahren')).toBeVisible()
  await expect(page.getByText('Du hast schon nach einem strukturierten Plan mit Intervallen trainiert')).toBeVisible()
  await page.getByRole('button', { name: '3–6 h' }).click()
  await expect(page.getByTestId('pinned-inquiry')).toContainText('Hast du schon einmal nach einem Plan')
  // A failed save: the answer cannot be sent, and the pin says so in German.
  // It read "Couldn't send that answer — try again." (ai-trainer-ops#268).
  await page.route('**/api/v1/users/me/inquiries/*/answer', (route) =>
    route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'Internal error' }) })
  )
  await page.getByTestId('pinned-inquiry').getByRole('button', { name: 'Nein, noch nie' }).click()
  await expect(page.getByText('Die Antwort ließ sich nicht senden. Versuch es noch einmal.')).toBeVisible()
  await page.unroute('**/api/v1/users/me/inquiries/*/answer')
  await noFormerEnglish(page, 'the dashboard')

  const input = page.getByPlaceholder('Frag deinen Coach …')
  const send = page.getByRole('button', { name: 'Nachricht senden' })

  // A 402: the spent allowance, numbers and the place to go included.
  await page.route('**/api/v1/ai/ask-trainer', (route) =>
    route.fulfill({
      status: 402,
      contentType: 'application/json',
      body: JSON.stringify({
        detail:
          'You have used the free coaching allowance: 5,000,000 of 5,000,000 tokens in the last 30 days. ' +
          'Add your own AI key (Gemini or OpenAI) in Settings to keep going without a limit, or get in touch ' +
          'with whoever runs this coach. The allowance frees up again as older usage ages out of the 30-day window.',
      }),
    })
  )
  await input.fill('Wie sieht morgen aus?')
  await send.click()
  await expect(page.getByText(/Du hast das freie Coaching-Kontingent aufgebraucht: 5\.000\.000/)).toBeVisible()
  await expect(page.getByText(/Coach & KI → Dein KI-Schlüssel/).first()).toBeVisible()

  // A 502: the model did not answer.
  await page.unroute('**/api/v1/ai/ask-trainer')
  await page.route('**/api/v1/ai/ask-trainer', (route) =>
    route.fulfill({
      status: 502,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'The AI model did not answer just now. Nothing was changed. Try again in a minute.' }),
    })
  )
  await input.fill('Und übermorgen?')
  await send.click()
  await expect(
    page.getByText(/Entschuldige, ich konnte nicht antworten\. Das KI-Modell hat gerade nicht geantwortet\./)
  ).toBeVisible()
  await noFormerEnglish(page, 'the chat after a 402 and a 502')
})
