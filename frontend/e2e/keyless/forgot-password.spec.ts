/**
 * A forgotten password on a server that sends no mail (ai-trainer-ops#35).
 *
 * The stack here has no SMTP_HOST, which is how a fresh deployment starts. The
 * reset endpoint then answers 503, and the page has to turn that into the one
 * useful sentence instead of an error. A component test mocks the 503; this
 * checks that the real backend gives it and the page reads it.
 */

import { expect, test } from '@playwright/test'

test('without mail, the forgot-password page says whom to ask', async ({ page }) => {
  await page.goto('/login')
  await page.getByRole('link', { name: 'Forgot your password?' }).click()

  await expect(page).toHaveURL(/\/forgot-password$/)
  await page.getByLabel('Email').fill('someone@aitrainer-e2e.com')
  await page.getByRole('button', { name: 'Send the link' }).click()

  await expect(page.getByText(/Ask the person who invited you/)).toBeVisible()
})
