/**
 * Layout defects that are measurable rather than visible (ai-trainer-ops#51).
 *
 * A screenshot shows a layout problem to a person. These assert the ones a
 * machine can decide, so they stay fixed: a page that scrolls sideways is
 * `scrollWidth > clientWidth`, and that is true or false without anyone's
 * judgement.
 *
 * Only pages a signed-out visitor can reach are covered. The dashboard, the day
 * view and Settings all need an onboarded account, which needs a plan, which
 * needs an AI key — so the defects reported there wait on the stub provider
 * named in ai-trainer-ops#46.
 */

import { expect, test } from '@playwright/test'

/** Every page a stranger can reach without an account. */
const PUBLIC_PAGES = ['/', '/login', '/register']

for (const path of PUBLIC_PAGES) {
  test(`${path} does not scroll sideways`, async ({ page }) => {
    await page.goto(path)

    const { scrollWidth, clientWidth, widest } = await page.evaluate(() => {
      let widest = { tag: '', className: '', right: 0 }
      for (const element of Array.from(document.querySelectorAll('*'))) {
        const box = element.getBoundingClientRect()
        if (box.right > widest.right) {
          widest = {
            tag: element.tagName,
            className: String((element as HTMLElement).className || '').slice(0, 80),
            right: Math.round(box.right),
          }
        }
      }
      return {
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
        widest,
      }
    })

    // Naming the widest element in the message, because "399 > 390" without it
    // is a failure someone has to reproduce before they can act on it. The
    // landing header was found exactly this way: an `<a>` carrying
    // `whitespace-nowrap` next to two other elements that also refused to
    // shrink.
    expect(
      scrollWidth,
      `${path} overflows by ${scrollWidth - clientWidth}px; widest element: ` +
        `<${widest.tag} class="${widest.className}"> reaching ${widest.right}px`
    ).toBeLessThanOrEqual(clientWidth)
  })
}
