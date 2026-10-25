import { describe, expect, it } from 'vitest'
import { summary, tally } from './e2e-census.mjs'

const report = {
  suites: [
    {
      title: 'login.spec.ts',
      file: 'login.spec.ts',
      // As Playwright writes it: every repeat and viewport its own spec entry.
      specs: [
        { title: 'signs in', tests: [{ projectName: 'desktop', status: 'expected' }] },
        { title: 'signs in', tests: [{ projectName: 'desktop', status: 'unexpected' }] },
        { title: 'signs in', tests: [{ projectName: 'mobile', status: 'expected' }] },
      ],
      suites: [
        {
          title: 'a describe block',
          specs: [{ title: 'signs out', tests: [{ projectName: 'desktop', status: 'skipped' }] }],
        },
      ],
    },
  ],
}

describe('the flake census (ai-trainer-ops#291)', () => {
  it('counts each test per stack and viewport, and leaves skips out', () => {
    const counts = tally([['keyless', report]])

    expect(Object.fromEntries(counts)).toEqual({
      'keyless: login.spec.ts › signs in [desktop]': { runs: 2, failures: 1 },
      'keyless: login.spec.ts › signs in [mobile]': { runs: 1, failures: 0 },
    })
  })

  it('lists what failed at least once, and says so when nothing did', () => {
    const red = summary(tally([['keyless', report]]))
    expect(red.failing).toBe(1)
    expect(red.text).toContain('| keyless: login.spec.ts › signs in [desktop] | 1 of 2 |')

    const green = summary(tally([['stubbed', { suites: [] }]]))
    expect(green.failing).toBe(0)
    expect(green.text).toContain('Every test passed in every run.')
  })
})
