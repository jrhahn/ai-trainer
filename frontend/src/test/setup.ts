import { beforeEach, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'

// jsdom does not implement scrollIntoView
window.HTMLElement.prototype.scrollIntoView = () => {}
// nor scrolling the window
window.scrollTo = () => {}

// Every test starts on the same clock: today at midday, standing still
// (ai-trainer-ops#291). Many tests work out "today" when they load or set up,
// and the component works it out again when it renders. In a run that crossed
// 00:00 UTC, which is when night shifts merge, the two were different days and
// the test failed. Only `Date` is faked: faking the timers too would break
// `userEvent` and `waitFor`. A test that needs its own clock sets one, and
// the next test starts on this one again, after a file's `useRealTimers()` too.
const MIDDAY = new Date()
MIDDAY.setHours(12, 0, 0, 0)

function pinClock() {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(MIDDAY)
}

pinClock()
beforeEach(pinClock)
