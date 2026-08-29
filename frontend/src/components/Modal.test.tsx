import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import Modal from './Modal'

function renderModal(open: boolean, onClose = vi.fn()) {
  render(
    <Modal open={open} onClose={onClose} title="Training calendar">
      <button type="button">Inside</button>
    </Modal>
  )
  return onClose
}

describe('Modal', () => {
  it('renders nothing while closed', () => {
    const { container } = render(
      <Modal open={false} onClose={vi.fn()} title="Training calendar">
        <p>Body</p>
      </Modal>
    )

    expect(container).toBeEmptyDOMElement()
  })

  it('exposes the panel as a labelled dialog', () => {
    renderModal(true)

    expect(screen.getByRole('dialog', { name: 'Training calendar' })).toBeInTheDocument()
    expect(screen.getByText('Inside')).toBeInTheDocument()
  })

  it('closes on Escape', async () => {
    const onClose = renderModal(true)

    await userEvent.keyboard('{Escape}')

    expect(onClose).toHaveBeenCalled()
  })

  it('closes on the close button', async () => {
    const onClose = renderModal(true)

    await userEvent.click(screen.getByRole('button', { name: 'Close' }))

    expect(onClose).toHaveBeenCalled()
  })

  it('closes on the backdrop but not on the panel itself', async () => {
    const onClose = renderModal(true)

    await userEvent.click(screen.getByRole('dialog'))
    expect(onClose).not.toHaveBeenCalled()

    // The backdrop is the panel's parent; clicking it is how a mouse user
    // dismisses the overlay.
    await userEvent.click(screen.getByRole('dialog').parentElement!)
    expect(onClose).toHaveBeenCalled()
  })

  it('locks the page behind it so only the panel scrolls', () => {
    renderModal(true)

    expect(document.body.style.overflow).toBe('hidden')
  })

  it('moves focus into the panel and keeps Tab there', async () => {
    renderModal(true)
    const dialog = screen.getByRole('dialog')
    const inside = screen.getByRole('button', { name: 'Inside' })
    const close = screen.getByRole('button', { name: 'Close' })

    expect(dialog).toContainElement(document.activeElement as HTMLElement)

    // Tabbing off the last control wraps to the first rather than escaping to
    // the page underneath.
    inside.focus()
    await userEvent.tab()
    expect(document.activeElement).toBe(close)
  })
})
