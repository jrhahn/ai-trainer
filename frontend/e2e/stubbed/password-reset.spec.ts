/**
 * A forgotten password, end to end (ai-trainer-ops#35).
 *
 * The unit tests pin each half: the backend's tokens and sessions, and each
 * page on its own. What they cannot see is the joint between them: the link the
 * mail carries, the route that opens it, the token the page reads from the
 * fragment and sends back, and a sign-in with the new password at the end.
 * Change any one of those alone and every unit test stays green while resets
 * stop working. This walks the whole path against the real backend, with a
 * local mail server (`e2e/mail_catcher.py`) in place of the provider.
 */

import { expect, test, type APIRequestContext } from '@playwright/test'
import { MAIL_CATCHER_HTTP_PORT } from '../../playwright.stub.config'

const NEW_PASSWORD = 'a fresh climb every morning'

function newAthlete() {
  const stamp = `${Date.now()}-${Math.floor(Math.random() * 1e6)}`
  return {
    name: 'Robin Forgetful',
    email: `e2e-reset-${stamp}@aitrainer-e2e.com`,
    password: 'Str0ng!Passw0rd',
  }
}

/** The reset link in the mail to *email*, once it has arrived. */
async function resetLink(request: APIRequestContext, email: string): Promise<string> {
  let link = ''
  await expect
    .poll(
      async () => {
        const messages: { to: string[]; text: string }[] = await (
          await request.get(`http://127.0.0.1:${MAIL_CATCHER_HTTP_PORT}/messages`)
        ).json()
        const mail = messages.find((message) => message.to.includes(email))
        link = mail?.text.match(/\S+\/reset-password#token=[\w-]+/)?.[0] ?? ''
        return link
      },
      { timeout: 20_000, message: `a reset mail to ${email}` }
    )
    .not.toBe('')
  return link
}

test('a forgotten password comes back through the mailed link', async ({ page, browser, request }) => {
  const athlete = newAthlete()
  await page.goto('/register')
  await page.getByPlaceholder('Your name').fill(athlete.name)
  await page.getByPlaceholder('you@example.com').fill(athlete.email)
  await page.getByPlaceholder('Strong password').fill(athlete.password)
  await page.getByPlaceholder('Repeat your password').fill(athlete.password)
  // Health data need explicit consent (ai-trainer-ops#2).
  await page.getByRole('checkbox', { name: /privacy policy/i }).check()
  await page.getByRole('button', { name: 'Create Account' }).click()
  await expect(page.getByText(`Welcome, ${athlete.name}!`).first()).toBeVisible({ timeout: 30_000 })

  // Signed out, in a browser of its own: where someone who forgot it starts.
  const elsewhere = await browser.newContext({ baseURL: test.info().project.use.baseURL })
  const tab = await elsewhere.newPage()
  await tab.goto('/login')
  await tab.getByRole('link', { name: 'Forgot your password?' }).click()
  await tab.getByLabel('Email').fill(athlete.email)
  await tab.getByRole('button', { name: 'Send the link' }).click()
  await expect(tab.getByRole('status')).toContainText('If this address has an account')

  await tab.goto(await resetLink(request, athlete.email))
  await tab.getByLabel('New password', { exact: true }).fill(NEW_PASSWORD)
  await tab.getByLabel('Confirm new password').fill(NEW_PASSWORD)
  await tab.getByRole('button', { name: 'Set the new password' }).click()
  await expect(tab.getByRole('status')).toContainText('every session is signed out')

  await tab.goto('/login')
  await tab.getByPlaceholder('you@example.com').fill(athlete.email)
  await tab.getByPlaceholder('Your password').fill(NEW_PASSWORD)
  await tab.getByRole('button', { name: 'Sign In' }).click()
  await expect(tab.getByText(`Welcome, ${athlete.name}!`).first()).toBeVisible({ timeout: 30_000 })

  await elsewhere.close()
})
