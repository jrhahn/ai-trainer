/**
 * No emoji in the interface (ai-trainer-ops#51.7).
 *
 * The issue's complaint was concrete: the test browser has no emoji font, so
 * 🚴 and 😴 rendered as empty boxes. On a real device most of them appear, but
 * as whatever the platform's font decides — a different drawing on every phone,
 * next to the lucide set used everywhere else.
 *
 * This walks the text actually on screen rather than grepping the source. A
 * source scan has to tell a glyph in a comment from a glyph in a string, and
 * the comments explaining this change are full of the emoji it removed. What is
 * on screen has no such ambiguity.
 *
 * Icons are not covered here and do not need to be: they are SVG, so they leave
 * no text to find.
 */

import { expect, test, type Page } from '@playwright/test'

/* Emoji and the pictographic dingbats, which is where every offender in #51.7
 * lived: 🚴😴💪 and friends in the Supplemental Symbols blocks, ⚠ ✅ ✕ ✓ in
 * Miscellaneous Symbols and Dingbats, plus the variation selector that makes
 * ❤️ out of ❤. Deliberately not arrows, dashes or ×, which are typography. */
const EMOJI = /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{FE0F}\u{2B00}-\u{2BFF}]/u

function newAthlete() {
  const stamp = `${Date.now()}-${Math.floor(Math.random() * 1e6)}`
  return {
    name: 'Robin Plain',
    email: `e2e-plain-${stamp}@aitrainer-e2e.com`,
    password: 'Str0ng!Passw0rd',
  }
}

/** Every text node on the page that holds an emoji, with where it sits. */
async function emojiOnScreen(page: Page) {
  return page.evaluate((source) => {
    const pattern = new RegExp(source, 'u')
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
    const found: { text: string; where: string }[] = []
    let node: Node | null
    while ((node = walker.nextNode())) {
      const text = (node.textContent || '').trim()
      if (!text || !pattern.test(text)) continue
      const element = node.parentElement
      found.push({
        text: text.slice(0, 80),
        where: element
          ? `<${element.tagName.toLowerCase()} class="${String(element.className).slice(0, 60)}">`
          : '(detached)',
      })
    }
    return found
  }, EMOJI.source)
}

async function expectNoEmoji(page: Page, screen: string) {
  const found = await emojiOnScreen(page)
  expect(
    found,
    `emoji still on ${screen}: ${found.map((f) => `"${f.text}" in ${f.where}`).join('; ')}`
  ).toEqual([])
}

test.describe('the interface draws icons, not emoji', () => {
  test('from registration through to the plan', async ({ page }) => {
    const athlete = newAthlete()

    await page.goto('/register')
    await expect(page.getByPlaceholder('Your name')).toBeVisible()
    await expectNoEmoji(page, 'the registration form')

    await page.getByPlaceholder('Your name').fill(athlete.name)
    await page.getByPlaceholder('you@example.com').fill(athlete.email)
    await page.getByPlaceholder('Strong password').fill(athlete.password)
    await page.getByPlaceholder('Repeat your password').fill(athlete.password)
    await page.getByRole('button', { name: 'Create Account' }).click()

    // The welcome step, which had the waving hand and three ticks.
    await expect(page.getByText(`Welcome, ${athlete.name}!`).first()).toBeVisible({
      timeout: 30_000,
    })
    await expectNoEmoji(page, 'the onboarding welcome')

    for (let step = 0; step < 4; step++) {
      // Each step in turn: the goal cards carried a trophy and a bicep.
      await expectNoEmoji(page, `onboarding step ${step + 1}`)
      await page.getByRole('button', { name: /^continue$/i }).first().click()
    }
    await page.getByRole('button', { name: /generate my 14-day training plan/i }).click()

    // The dashboard, which had the greeting's hand and the leg-feel circles.
    await expect(page.getByRole('heading', { name: /robin/i }).first()).toBeVisible({
      timeout: 60_000,
    })
    await expect(page.getByRole('heading', { name: /your week/i }).first()).toBeVisible()
    await expectNoEmoji(page, 'the dashboard')

    // And the month calendar, which is where most of them were.
    await page.getByRole('button', { name: /show more/i }).click()
    await expect(page.getByRole('button', { name: /^Calendar day/ }).first()).toBeVisible()
    await expectNoEmoji(page, 'the month calendar')
  })

  test('the calendar still says what each day is', async ({ page }) => {
    // The guard the test above needs: a page that rendered nothing at all would
    // satisfy "no emoji" perfectly. The sessions have to be there, and drawn.
    const athlete = newAthlete()

    await page.goto('/register')
    await page.getByPlaceholder('Your name').fill(athlete.name)
    await page.getByPlaceholder('you@example.com').fill(athlete.email)
    await page.getByPlaceholder('Strong password').fill(athlete.password)
    await page.getByPlaceholder('Repeat your password').fill(athlete.password)
    await page.getByRole('button', { name: 'Create Account' }).click()
    await expect(page.getByText(`Welcome, ${athlete.name}!`).first()).toBeVisible({
      timeout: 30_000,
    })
    for (let step = 0; step < 4; step++) {
      await page.getByRole('button', { name: /^continue$/i }).first().click()
    }
    await page.getByRole('button', { name: /generate my 14-day training plan/i }).click()
    await expect(page.getByRole('heading', { name: /robin/i }).first()).toBeVisible({
      timeout: 60_000,
    })
    await page.getByRole('button', { name: /show more/i }).click()

    const drawn = await page.evaluate(() => {
      const cells = Array.from(document.querySelectorAll('button[aria-label^="Calendar day"]'))
      const withSession = cells.filter((cell) => /\[stub\]/.test(cell.textContent || ''))
      return {
        withSession: withSession.length,
        withIcon: withSession.filter((cell) => cell.querySelector('svg.lucide')).length,
        // The stub cycles ride, lift, rest, so all three icons should appear.
        icons: new Set(
          Array.from(document.querySelectorAll('button[aria-label^="Calendar day"] svg.lucide'))
            .map((svg) => String(svg.getAttribute('class')))
            .filter((name) => /lucide-(bike|dumbbell|moon)\b/.test(name))
            .map((name) => name.replace(/.*(lucide-(?:bike|dumbbell|moon))\b.*/, '$1'))
        ).size,
      }
    })

    expect(drawn.withSession, 'the calendar should show the stub plan').toBeGreaterThan(5)
    expect(drawn.withIcon, 'every planned day should be drawn').toBe(drawn.withSession)
    expect(drawn.icons, 'ride, lift and rest should look different').toBe(3)
  })
})
