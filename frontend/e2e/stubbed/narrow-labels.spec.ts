/**
 * Nothing on a phone is clipped, stretched or pushed out of shape by the length
 * of a word (ai-trainer-ops#51.4, #51.5).
 *
 * Both defects are the same defect: a label sized by its own text inside a
 * column that does not grow. Both are only visible with a plan on the screen,
 * which is why neither could be measured before the stubbed stack existed.
 *
 * Measured with `Range` rather than `scrollWidth`. `scrollWidth` only differs
 * from `clientWidth` when something clips — so it catches `truncate` and misses
 * plain overflow, and the week strip's first fix was exactly to stop clipping.
 * A range over the text node reports where the text was laid out, whether or
 * not it is painted, so one number covers both.
 */

import { expect, test, type Page } from '@playwright/test'

function newAthlete() {
  const stamp = `${Date.now()}-${Math.floor(Math.random() * 1e6)}`
  return {
    name: 'Robin Narrow',
    email: `e2e-narrow-${stamp}@aitrainer-e2e.com`,
    password: 'Str0ng!Passw0rd',
  }
}

async function onboard(page: Page, athlete: ReturnType<typeof newAthlete>) {
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
  await expect(page.getByRole('heading', { name: /your week/i }).first()).toBeVisible()
}

/** Local date, offset days from today, in the form the app's URLs use. */
function localDate(offset: number) {
  const date = new Date()
  date.setDate(date.getDate() + offset)
  return [
    date.getFullYear(),
    String(date.getMonth() + 1).padStart(2, '0'),
    String(date.getDate()).padStart(2, '0'),
  ].join('-')
}

/** The feedback dialog for one day of the stub plan.
 *
 * Through the month calendar, because that is the only route to a workout page:
 * the week-strip chips select rather than navigate (#634), and a direct
 * `/workout/:date` load redirects to `/` while the token is still rehydrating.
 */
async function openFeedbackForm(page: Page, dayOffset: number) {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: /robin/i }).first()).toBeVisible({
    timeout: 60_000,
  })
  await page.getByRole('button', { name: /show more/i }).click()
  await page.getByRole('button', { name: `Calendar day ${localDate(dayOffset)}` }).click()
  await page.getByRole('button', { name: /log completed workout/i }).click()
  await expect(page.getByText('Perceived Effort *')).toBeVisible()
}

test.describe('on a phone, a long word does not break the layout', () => {
  test('every week-strip label fits inside its own chip', async ({ page }) => {
    // ai-trainer-ops#51.4. Seven columns leave 36 px at 390 px and "Endurance"
    // was laid out at 58, so three chips in a row read "Endur…" — all cut in
    // the same place, which is also the least informative place to cut.
    const athlete = newAthlete()
    await onboard(page, athlete)

    const labels = await page.evaluate(() => {
      const heading = Array.from(document.querySelectorAll('h2')).find(
        (h) => (h.textContent || '').trim().toLowerCase() === 'your week'
      )
      const strip = heading?.closest('section')
      if (!strip) return null

      const out: { text: string; textWidth: number; chipWidth: number }[] = []
      for (const chip of Array.from(strip.querySelectorAll('button[aria-pressed]'))) {
        const style = getComputedStyle(chip)
        const chipWidth =
          chip.clientWidth -
          parseFloat(style.paddingLeft) -
          parseFloat(style.paddingRight)
        for (const span of Array.from(chip.querySelectorAll('span'))) {
          // Leaves only, and only ones actually on screen: the chip holds a
          // narrow and a wide variant of the label and hides one of them.
          if (span.children.length > 0) continue
          if (span.getClientRects().length === 0) continue
          const text = (span.textContent || '').trim()
          if (!text || text === '—') continue
          const range = document.createRange()
          range.selectNodeContents(span)
          out.push({ text, textWidth: range.getBoundingClientRect().width, chipWidth })
        }
      }
      return out
    })

    expect(labels, 'the week strip should be on the page').not.toBeNull()
    expect(labels!.length, 'the strip should have labels to measure').toBeGreaterThan(0)

    const tooWide = labels!.filter((l) => l.textWidth > l.chipWidth + 0.5)
    expect(
      tooWide,
      `labels wider than their chip: ${tooWide
        .map((l) => `"${l.text}" ${l.textWidth.toFixed(1)}px in ${l.chipWidth.toFixed(1)}px`)
        .join('; ')}`
    ).toEqual([])
  })

  test('each day in the strip names its type with an icon, not only a colour', async ({
    page,
  }) => {
    // The other half of #51.4's ask, and the reason it is worth doing: an
    // endurance day and a race were a green dot and a red dot, which is one dot
    // to an athlete who cannot tell those apart (WCAG 1.4.1).
    const athlete = newAthlete()
    await onboard(page, athlete)

    const withIcons = await page.evaluate(() => {
      const heading = Array.from(document.querySelectorAll('h2')).find(
        (h) => (h.textContent || '').trim().toLowerCase() === 'your week'
      )
      const chips = Array.from(
        heading?.closest('section')?.querySelectorAll('button[aria-pressed]') ?? []
      )
      // Days with a session, told apart by their label rather than by their
      // looks: the chip's accessible name says so in words.
      const planned = chips.filter(
        (chip) => !/nothing planned/i.test(chip.getAttribute('aria-label') || '')
      )
      return {
        planned: planned.length,
        withSvg: planned.filter((chip) => chip.querySelector('svg')).length,
      }
    })

    expect(withIcons.planned, 'the stub plan covers the days from today on').toBeGreaterThan(0)
    expect(withIcons.withSvg).toBe(withIcons.planned)
  })

  // Both wordings. The strength scale carries the longest labels in the app
  // ("Controlled", "Challenging"), and it is the one that was never on screen
  // in a test before the stub plan grew a lift day.
  for (const [what, dayOffset, expectedForTwo] of [
    ['a ride', 0, 'Moderate'],
    ['a strength session', 1, 'Controlled'],
  ] as const) {
    test(`the effort scale for ${what} is five equal buttons inside the dialog`, async ({
      page,
    }) => {
      // ai-trainer-ops#51.5. On `flex-1`, which sizes to content, "Moderate"
      // made its button 59 px against the others' 55 and the row came to 311 px
      // inside a 310 px dialog. The dialog is `max-w-md`, so the columns never
      // exceed ~74 px on any screen and "Challenging" needs more than that —
      // there was no width at which the old layout worked.
      const athlete = newAthlete()
      await onboard(page, athlete)
      await openFeedbackForm(page, dayOffset)

      const scale = await page.evaluate(() => {
        const row = document.querySelector('[aria-labelledby="effort-label"]') as HTMLElement
        const buttons = Array.from(row.querySelectorAll('button'))
        const dialog = row.closest('.overflow-y-auto') as HTMLElement
        return {
          widths: buttons.map((b) => Math.round(b.getBoundingClientRect().width)),
          names: buttons.map((b) => b.getAttribute('aria-label')),
          rowWidth: row.getBoundingClientRect().width,
          available: row.parentElement!.clientWidth,
          dialogOverflow: dialog.scrollWidth - dialog.clientWidth,
          pageOverflow:
            document.documentElement.scrollWidth - document.documentElement.clientWidth,
        }
      })

      expect(scale.widths).toHaveLength(5)
      // Equal to the pixel. This is the assertion the old layout failed, and it
      // fails for any label long enough to matter rather than for one word.
      expect(new Set(scale.widths), `button widths: ${scale.widths.join(', ')}`).toHaveProperty(
        'size',
        1
      )
      expect(scale.rowWidth).toBeLessThanOrEqual(scale.available + 0.5)
      expect(scale.dialogOverflow, 'the dialog should not scroll sideways').toBeLessThanOrEqual(0)
      expect(scale.pageOverflow, 'the page should not scroll sideways').toBeLessThanOrEqual(0)

      // Every level is still named, which is the thing the fix could have
      // quietly cost: the words left the buttons' faces, not their labels.
      expect(scale.names).toEqual([
        expect.stringContaining('Easy'),
        expect.stringContaining(expectedForTwo),
        expect.any(String),
        expect.stringContaining('Very Hard'),
        expect.stringContaining('Max'),
      ])
    })
  }
})
