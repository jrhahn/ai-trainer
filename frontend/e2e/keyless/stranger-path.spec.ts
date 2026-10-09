/**
 * The path a stranger takes (ai-trainer-ops#46).
 *
 * Two bugs broke registration for every new athlete and neither was caught:
 *
 * * onboarding overwrote the name the athlete registered with (#40), and
 * * in BYOK-only mode onboarding ended on step 5 with no way forward (#41) —
 *   it now asks for the key as a step of its own, which is what is checked.
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
  test('onboarding greets the athlete by the name they registered with', async ({ page }) => {
    // Regression guard for ai-trainer-ops#40. Onboarding used to send the name
    // field back to the server from a form that had copied it before the
    // profile loaded, so the athlete's name was replaced by an empty string.
    const errors = watchConsole(page)
    const athlete = newAthlete()

    await register(page, athlete)

    // Onboarding is where registration lands, and the greeting carries the name
    // rather than the 'athlete' fallback. Asserted as one anchored string:
    // `/Alex/i` matched any text on the page containing those letters, which is
    // a greeting assertion that would have survived losing the greeting.
    await expect(
      page.getByText(`Welcome, ${athlete.name}!`, { exact: false }).first()
    ).toBeVisible({ timeout: 30_000 })

    expect(errors, `console errors: ${errors.join(' | ')}`).toEqual([])
  })

  test('the BYOK requirement is asked for, and onboarding does not claim to be finished', async ({
    page,
  }) => {
    // Regression guard for ai-trainer-ops#41, both halves — and the assertions
    // moved when the fix landed, which is worth spelling out.
    //
    // This test used to walk to the summary, press "Generate", and check that
    // the 402 was *named* rather than appearing as "Failed to fetch". That was
    // the best available outcome while the athlete had nowhere to go: the one
    // thing it could ask for was a message. Onboarding now asks for the key as
    // a step before the summary, so "Generate" is never reached and there is no
    // 402 to name. Asserting the old message would mean asserting that the dead
    // end is still there.
    //
    // What has not changed is the second half: the account must not be left
    // marked onboarded with no plan, because a reload would then land on an
    // empty dashboard with nothing naming the cause. That is still the reload
    // below.
    const errors = watchConsole(page)
    const athlete = newAthlete()

    await register(page, athlete)
    await expect(page.getByText(/^Welcome,/).first()).toBeVisible({ timeout: 30_000 })

    // Walk the questions. There are six steps in this mode, not five.
    for (let step = 0; step < 4; step++) {
      await page.getByRole('button', { name: /^continue$/i }).first().click()
    }

    // Asked for, in a form the athlete can fill — not told after the fact.
    await expect(page.getByRole('heading', { name: /add your gemini key/i })).toBeVisible({
      timeout: 30_000,
    })
    await expect(page.getByText('Ready to Go!')).toHaveCount(0)

    // And still in onboarding after a reload. Progress is restored from
    // sessionStorage, so the athlete returns to the step they were on rather
    // than to the greeting — asserting the step-1 greeting here was wrong once
    // before, and the run said so.
    await page.reload()
    await expect(page.getByRole('heading', { name: /add your gemini key/i })).toBeVisible({
      timeout: 30_000,
    })
    await expect(page.getByText(/no session planned for today/i)).toHaveCount(0)

    // The ai-trainer-ops#40 assertion that used to live here — the registered
    // name coming back from the server *after* `updateCurrentUser`, which is
    // the only form of it that can catch the overwrite — has moved to
    // `e2e/stubbed/dashboard.spec.ts`.
    //
    // It had to. In this mode onboarding now stops at the key step, and the
    // profile write happens in `handleGenerate`, one step further on. There is
    // no profile round trip left in this flow to observe, so an assertion here
    // would be checking a name that never left the browser. The stubbed stack
    // completes onboarding, so the same check runs there with a real write and
    // a reload behind it.

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
