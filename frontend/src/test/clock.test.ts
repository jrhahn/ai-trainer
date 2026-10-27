import { describe, expect, it, vi } from 'vitest'

// The suite's clock (`src/test/setup.ts`, ai-trainer-ops#291). Taken when this
// file loads, as a test's "today" so often is.
const LOADED_AT = new Date()

/** Waits on the real clock: the faked `Date` would not move. */
function realWait(ms: number) {
  const start = performance.now()
  while (performance.now() - start < ms) {
    // busy: a timer would let the test end first
  }
}

const atMidday = (date: Date) =>
  [date.getHours(), date.getMinutes(), date.getSeconds(), date.getMilliseconds()].join(':') === '12:0:0:0'

describe('the test clock (ai-trainer-ops#291)', () => {
  it('stands at midday already while a file loads', () => {
    expect(atMidday(LOADED_AT)).toBe(true)
  })

  it('starts every test at midday, standing still', () => {
    const start = Date.now()
    realWait(5)

    expect(atMidday(new Date())).toBe(true)
    expect(Date.now()).toBe(start)
  })

  it('lets a test leave it', () => {
    vi.useRealTimers()
    const start = Date.now()
    realWait(5)

    expect(Date.now()).toBeGreaterThan(start)
  })

  it('starts the next test on it again', () => {
    const start = Date.now()
    realWait(5)

    expect(atMidday(new Date())).toBe(true)
    expect(Date.now()).toBe(start)
  })
})
