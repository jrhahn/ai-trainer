import { StrictMode, useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useBackGesture } from './useBackGesture'

// The phone's back gesture closes an open overlay instead of leaving the page
// (review of 2026-11-01, ai-trainer-ops#338 A12).

function Overlay({ open, onClose }: { open: boolean; onClose: () => void }) {
  useBackGesture(open, onClose)
  return null
}

/** Onboarding's shape: back goes one step back, until the first. */
function Steps() {
  const [step, setStep] = useState(1)
  useBackGesture(step > 1, () => setStep((s) => s - 1), step)
  return (
    <button type="button" onClick={() => setStep(step + 1)}>
      Step {step}
    </button>
  )
}

const overlayDepth = () => (window.history.state as { overlay?: number } | null)?.overlay
const settled = () => waitFor(() => expect(overlayDepth()).toBeUndefined())

afterEach(async () => {
  await settled()
})

describe('useBackGesture', () => {
  it('opens on its own history entry, keeping what the router stored', () => {
    window.history.replaceState({ key: 'page', idx: 3 }, '')
    const before = window.history.length

    const view = render(<Overlay open onClose={vi.fn()} />)

    expect(window.history.length).toBe(before + 1)
    expect(window.history.state).toEqual({ key: 'page', idx: 3, overlay: 1 })
    view.unmount()
  })

  it('closes on back and leaves the page where it was', async () => {
    const onClose = vi.fn()
    const path = window.location.pathname
    render(<Overlay open onClose={onClose} />)

    window.history.back()

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1))
    expect(window.location.pathname).toBe(path)
    expect(overlayDepth()).toBeUndefined()
  })

  it('takes its entry off again when closed some other way', async () => {
    const onClose = vi.fn()
    const view = render(<Overlay open onClose={onClose} />)
    expect(overlayDepth()).toBe(1)

    view.rerender(<Overlay open={false} onClose={onClose} />)

    await settled()
    // Its own back() is not read as a second close.
    expect(onClose).not.toHaveBeenCalled()
  })

  it('closes only the top overlay when two are open', async () => {
    const outer = vi.fn()
    const inner = vi.fn()
    render(
      <>
        <Overlay open onClose={outer} />
        <Overlay open onClose={inner} />
      </>
    )
    expect(overlayDepth()).toBe(2)

    window.history.back()

    await waitFor(() => expect(inner).toHaveBeenCalledTimes(1))
    expect(outer).not.toHaveBeenCalled()
    expect(overlayDepth()).toBe(1)
    window.history.back()
    await waitFor(() => expect(outer).toHaveBeenCalledTimes(1))
  })

  it('does not undo a navigation that closed it', async () => {
    const onClose = vi.fn()
    const view = render(<Overlay open onClose={onClose} />)

    // A link inside the overlay replaced its entry with the next page.
    window.history.replaceState({ key: 'next' }, '')
    view.rerender(<Overlay open={false} onClose={onClose} />)
    await new Promise((resolve) => setTimeout(resolve, 100))

    expect(window.history.state).toEqual({ key: 'next' })
  })

  it('does not undo a navigation right after the close either', async () => {
    const onClose = vi.fn()
    const view = render(<Overlay open onClose={onClose} />)

    // A save that closes the dialog and then moves on to the next page.
    view.rerender(<Overlay open={false} onClose={onClose} />)
    window.history.pushState({ key: 'next' }, '')
    await new Promise((resolve) => setTimeout(resolve, 100))

    expect(window.history.state).toEqual({ key: 'next' })
  })

  it('pushes one entry under StrictMode, whose remount would otherwise stack a second', async () => {
    const onClose = vi.fn()
    const before = window.history.length

    render(
      <StrictMode>
        <Overlay open onClose={onClose} />
      </StrictMode>
    )
    await new Promise((resolve) => setTimeout(resolve, 100))

    expect(window.history.length).toBe(before + 1)
    expect(overlayDepth()).toBe(1)
    expect(onClose).not.toHaveBeenCalled()
    window.history.back()
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1))
  })

  it('goes back one step at a time and leaves the page from the first', async () => {
    const path = window.location.pathname
    render(<Steps />)
    await userEvent.click(screen.getByRole('button', { name: 'Step 1' }))
    await userEvent.click(screen.getByRole('button', { name: 'Step 2' }))
    expect(screen.getByRole('button', { name: 'Step 3' })).toBeInTheDocument()
    // One entry for all the steps, not one per step.
    expect(overlayDepth()).toBe(1)

    window.history.back()
    await screen.findByRole('button', { name: 'Step 2' })
    await waitFor(() => expect(overlayDepth()).toBe(1))

    window.history.back()
    await screen.findByRole('button', { name: 'Step 1' })
    await settled()
    expect(window.location.pathname).toBe(path)
  })
})
