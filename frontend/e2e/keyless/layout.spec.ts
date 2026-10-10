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
    // Measured once the app has rendered, which waits for the session check
    // (ai-trainer-ops#45); an empty page would pass vacuously.
    await page.locator('#root > *').first().waitFor()

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

/**
 * On a phone the sign-in form comes before the marketing panel — on screen and
 * in the document (ai-trainer-ops#44.3).
 *
 * Measured before the fix at 390x844: the panel is 595 px tall and came first,
 * so the email field started at y=780 and a returning athlete scrolled almost a
 * full screen before every sign-in.
 *
 * Two assertions, and both are needed. Position alone passed the first fix,
 * which moved the form with `order-*` and left it second in the document, so
 * tab order and the screen reader still met the panel first. Document order
 * alone would not notice a layout that paints them the other way round.
 */
for (const path of ['/login', '/register']) {
  test(`${path} puts the form before the marketing panel, on screen and in the document`, async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'mobile-390', 'about the narrow layout')
    await page.goto(path)
    // The app renders once the session check has answered (ai-trainer-ops#45),
    // which is after `goto` resolves.
    await page.getByTestId('auth-form-panel').waitFor()

    const { formTop, panelTop, viewportHeight, domOrderIsFormFirst } = await page.evaluate(() => {
      const form = document.querySelector('form') as HTMLElement
      // By test id, not by `section.relative`: a styling class is not a
      // contract, and a layout change that dropped it would fail this test with
      // a null dereference rather than a statement about the layout.
      const panel = document.querySelector('[data-testid="auth-marketing-panel"]') as HTMLElement
      const formPanel = document.querySelector('[data-testid="auth-form-panel"]') as HTMLElement
      return {
        // Visual order is `order-*`; this is the order a keyboard and a screen
        // reader get, and the two must agree on a phone (WCAG 2.4.3, 1.3.2).
        domOrderIsFormFirst: Boolean(
          formPanel.compareDocumentPosition(panel) & Node.DOCUMENT_POSITION_FOLLOWING
        ),
        formTop: Math.round(form.getBoundingClientRect().top + window.scrollY),
        panelTop: Math.round(panel.getBoundingClientRect().top + window.scrollY),
        viewportHeight: window.innerHeight,
      }
    })

    // Ordering, not a pixel bound. The first version asserted
    // `formTop < viewportHeight`, which the bug already satisfied — the form
    // began at 780 px in an 844 px viewport, inside the first screen by the
    // letter of it and below the fold in every way that matters. A mutation run
    // said so: reverting the fix left that assertion green.
    expect(
      formTop,
      `${path}: form at ${formTop}px, marketing panel at ${panelTop}px, ` +
        `viewport ${viewportHeight}px — the form should come first`
    ).toBeLessThan(panelTop)

    expect(
      domOrderIsFormFirst,
      `${path}: the form is painted first but still comes second in the document, ` +
        'so tab order and the screen reader disagree with what is on screen'
    ).toBe(true)
  })
}

/** Password managers, passkeys and Chrome's own warning all key off these. */
const EXPECTED_AUTOCOMPLETE: Record<string, Record<string, string>> = {
  '/login': { 'input[type="email"]': 'email', 'input[type="password"]': 'current-password' },
  '/register': { 'input[type="email"]': 'email', 'input[type="text"]': 'name' },
}

for (const [path, fields] of Object.entries(EXPECTED_AUTOCOMPLETE)) {
  test(`${path} labels its fields for password managers`, async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'desktop-1366', 'not viewport-dependent')
    await page.goto(path)
    for (const [selector, expected] of Object.entries(fields)) {
      await expect(page.locator(selector).first()).toHaveAttribute('autocomplete', expected)
    }
  })
}

test('registration asks for a new password, not the saved one', async ({ page }, testInfo) => {
  // Both password fields, and `new-password` specifically: `current-password`
  // here would make a manager offer the existing entry instead of generating.
  test.skip(testInfo.project.name !== 'desktop-1366', 'not viewport-dependent')
  await page.goto('/register')
  const passwords = page.locator('input[type="password"]')
  await expect(passwords).toHaveCount(2)
  for (let index = 0; index < 2; index++) {
    await expect(passwords.nth(index)).toHaveAttribute('autocomplete', 'new-password')
  }
})
