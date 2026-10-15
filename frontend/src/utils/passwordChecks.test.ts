import { describe, expect, it, vi } from 'vitest'
import type { KeyboardEvent } from 'react'
import { getPasswordChecks, revealWhileHeld } from './passwordChecks'

const key = (k: string) => ({ key: k, preventDefault: vi.fn() }) as unknown as KeyboardEvent<HTMLButtonElement>

describe('getPasswordChecks', () => {
  it('passes a long phrase and fails a short or personal one', () => {
    expect(getPasswordChecks('gravel climbs at dawn', '', '').every((c) => c.valid)).toBe(true)
    expect(getPasswordChecks('short', '', '').find((c) => c.id === 'length')?.valid).toBe(false)
    expect(getPasswordChecks('robin rides a lot', 'Robin', '').find((c) => c.id === 'trivial')?.valid).toBe(false)
  })
})

describe('revealWhileHeld', () => {
  it('shows the password only while the pointer or the key is held', () => {
    const setVisible = vi.fn()
    const handlers = revealWhileHeld(setVisible)

    handlers.onPointerDown()
    handlers.onPointerUp()
    handlers.onPointerLeave()
    handlers.onPointerCancel()
    handlers.onBlur()
    handlers.onKeyDown(key(' '))
    handlers.onKeyUp(key('Enter'))
    handlers.onKeyDown(key('a'))

    expect(setVisible.mock.calls.map(([visible]) => visible)).toEqual([true, false, false, false, false, true, false])
  })
})
