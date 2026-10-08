/**
 * The path a stranger takes (ai-trainer-ops#46).
 *
 * Two bugs broke registration for every new athlete and neither was caught:
 *
 * * onboarding overwrote the name the athlete registered with (#40), and
 * * in BYOK-only mode onboarding ended on step 5 with no way forward (#41).
 *
 * Both are about what the browser shows after a sequence of requests, which is
 * the one thing a component test cannot see. Both are checked here.
 *
 * What this spec does *not* do, on purpose: it never asserts that a plan was
 * generated. The backend runs with `ALLOW_ADMIN_AI_KEY_FALLBACK=false` and no
 * provider key, so generation answers 402 — which is the state a fresh
 * deployment is in, and the one the dead end lived in. Asserting a *generated
 * plan* needs a stub provider, and a stub reachable from the application is a
 * thing to design carefully rather than add in passing; it is the follow-up
 * named in the issue comment.
 */

import { expect, test, type ConsoleMessage, type Page } from '@playwright/test'

/** A fresh athlete per run, so a persistent SQLite file is not a fixture.
 *
 * Not `@example.invalid`, which is what this suite was written with and which
 * registration rejected on the first run: `.invalid` is reserved by RFC 2606 and
 * `email-validator` refuses special-use domains. Correct of it — and a useful
 * first finding, since a fixture address that cannot register is a fixture that
 * silently tests nothing. Pydantic's `EmailStr` does not check deliverability,
 * so an ordinary unroutable domain is enough and no mail is ever sent.
 */
function newAthlete() {
  const stamp = `${Date.now()}-${Math.floor(Math.random() * 1e6)}`
  return {
    name: 'Alex Stranger',
    email: `e2e-${stamp}@aitrainer-e2e.com`,
    password: 'Str0ng!Passw0rd',
  }
}

/** Console errors the browser emits that are not the app's fault.
 *
 * Kept as an explicit, short list rather than a substring sweep: the issue asks
 * the job to fail on any console error, and an allowlist that grows by habit is
 * how that requirement quietly stops meaning anything.
 */
const IGNORED_CONSOLE = [
  // The 402 from plan generation is this suite's *expected* result, since the
  // backend runs with no usable AI key. Two lines appear for it and both are
  // allowed, pinned to that status and that path so the allowlist cannot grow
  // into "ignore failed requests":
  //   - the browser's own log for any non-2xx fetch, and
  //   - the app's api layer logging the same response.
  /Failed to load resource: the server responded with a status of 402/,
  /\[api\] Request failed \{method: POST, path: \/ai\/generate-plan, status: 402\b/,
]

function watchConsole(page: Page): string[] {
  const errors: string[] = []
  page.on('console', (message: ConsoleMessage) => {
    if (message.type() !== 'error') return
    const text = message.text()
    if (IGNORED_CONSOLE.some((pattern) => pattern.test(text))) return
    errors.push(text)
  })
  page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`))
  return errors
}

async function register(page: Page, athlete: ReturnType<typeof newAthlete>) {
  await page.goto('/')
  // The landing page is covered by asserting it offers the way in, and then the
  // route is used directly. Clicking a marketing CTA would couple this suite to
  // copy that is meant to change (ai-trainer-ops#53 rewrites it), and the
  // journey being tested starts at the form.
  await expect(page.locator('a[href="/register"]').first()).toBeVisible()
  await page.goto('/register')

  // Placeholders, not labels: the form has no <label for>, and asking by
  // placeholder is what a reader of the page would do.
  await page.getByPlaceholder('Your name').fill(athlete.name)
  await page.getByPlaceholder('you@example.com').fill(athlete.email)
  await page.getByPlaceholder('Strong password').fill(athlete.password)
  await page.getByPlaceholder('Repeat your password').fill(athlete.password)

  // The captcha is a SHA-256 proof of work solved in the page, so a real
  // browser solves it by being one — no stubbing, and no bypass to keep out of
  // production. It costs a second or two on a loaded runner.
  await page.getByRole('button', { name: 'Create Account' }).click()
}

test.describe('a stranger registers and reaches a usable app', () => {
  test('onboarding keeps the name the athlete registered with', async ({ page }) => {
    // Regression guard for ai-trainer-ops#40. Onboarding used to send the name
    // field back to the server from a form that had copied it before the
    // profile loaded, so the athlete's name was replaced by an empty string.
    const errors = watchConsole(page)
    const athlete = newAthlete()

    await register(page, athlete)

    // Onboarding is where registration lands.
    await expect(page.getByText(/^Welcome,/).first()).toBeVisible({ timeout: 30_000 })

    // The greeting has to carry the registered name, not a blank.
    await expect(page.getByText(new RegExp(athlete.name.split(' ')[0], 'i')).first()).toBeVisible()

    expect(errors, `console errors: ${errors.join(' | ')}`).toEqual([])
  })

  test('the BYOK dead end is named, and onboarding does not claim to be finished', async ({
    page,
  }) => {
    // Regression guard for ai-trainer-ops#41, both halves. With no usable AI
    // key the plan cannot be generated — the athlete must be *told*, and must
    // not be left marked onboarded with no plan, because a reload would then
    // land on an empty dashboard with nothing naming the cause.
    const errors = watchConsole(page)
    const athlete = newAthlete()

    await register(page, athlete)
    await expect(page.getByText(/^Welcome,/).first()).toBeVisible({ timeout: 30_000 })

    // Walk to the end of onboarding.
    for (let step = 0; step < 4; step++) {
      await page.getByRole('button', { name: /^continue$/i }).first().click()
    }
    await page.getByRole('button', { name: /generate my 14-day training plan/i }).click()

    // Told, in words an athlete can act on, rather than "Failed to fetch".
    await expect(page.getByText(/api key/i).first()).toBeVisible({ timeout: 30_000 })

    // And still in onboarding after a reload, which is where the message is.
    // The reload is the whole point: before #41 the server had already been told
    // `isOnboarded: true`, so this landed on a dashboard with no plan and
    // nothing naming the cause. Onboarding progress is restored from
    // sessionStorage, so the athlete returns to the last step rather than to the
    // greeting — asserting the step-1 greeting here was wrong, and the run said
    // so.
    await page.reload()
    await expect(
      page.getByRole('button', { name: /generate my 14-day training plan/i })
    ).toBeVisible({ timeout: 30_000 })
    await expect(page.getByText(/no session planned for today/i)).toHaveCount(0)

    // And the name survived the profile write. This is where ai-trainer-ops#40
    // is actually catchable: the greeting in the first test is read from the
    // store before onboarding writes anything, so only a name that came back
    // from the server *after* `updateCurrentUser` proves the field was not
    // overwritten. The issue asks for exactly this mutation to turn the job
    // red, and nothing else here would.
    await expect(page.getByText(athlete.name, { exact: true }).first()).toBeVisible()

    expect(errors, `console errors: ${errors.join(' | ')}`).toEqual([])
  })

  test('signing out and back in keeps the account', async ({ page }) => {
    const errors = watchConsole(page)
    const athlete = newAthlete()

    await register(page, athlete)
    await expect(page.getByText(/^Welcome,/).first()).toBeVisible({ timeout: 30_000 })

    // Registration leaves the athlete signed in, and an authenticated visit to
    // /login is routed away — so the session is dropped first. Clearing storage
    // rather than clicking a sign-out, because onboarding offers none: a
    // half-onboarded athlete who closes the tab is exactly what #45 is about,
    // and this reproduces that rather than a tidy logout.
    await page.evaluate(() => sessionStorage.clear())
    await page.goto('/login')
    await page.getByPlaceholder('you@example.com').fill(athlete.email)
    await page.getByPlaceholder('Your password').fill(athlete.password)
    await page.getByRole('button', { name: 'Sign In' }).click()

    await expect(page.getByText(/^Welcome,/).first()).toBeVisible({ timeout: 30_000 })

    expect(errors, `console errors: ${errors.join(' | ')}`).toEqual([])
  })
})
