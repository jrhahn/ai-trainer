import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import WrongFlag from './WrongFlag'
import { useAppStore } from '../store/useAppStore'

const { mockFlagWrong } = vi.hoisted(() => ({ mockFlagWrong: vi.fn() }))
vi.mock('../services/user', () => ({ flagWrong: mockFlagWrong }))

beforeEach(() => {
  mockFlagWrong.mockReset()
  useAppStore.setState({ authToken: 'tok' })
})

describe('WrongFlag (ai-trainer-ops#94)', () => {
  it('sends what was flagged and the optional note with one tap and one send', async () => {
    mockFlagWrong.mockResolvedValue(undefined)
    render(<WrongFlag target="reply" flagRef="2026-10-14T10:00:00Z" excerpt={'x'.repeat(5000)} />)

    await userEvent.click(screen.getByRole('button', { name: 'This is wrong' }))
    await userEvent.type(screen.getByRole('textbox', { name: 'What is wrong? (optional)' }), '  The race is Sunday.  ')
    await userEvent.click(screen.getByRole('button', { name: 'Send' }))

    expect(mockFlagWrong).toHaveBeenCalledWith('tok', {
      target: 'reply',
      ref: '2026-10-14T10:00:00Z',
      // The server keeps 4000 characters of a long reply and refuses more.
      excerpt: 'x'.repeat(4000),
      note: 'The race is Sunday.',
    })
    expect(await screen.findByRole('status')).toHaveTextContent('Thanks.')
  })

  it('sends no note when none was written', async () => {
    mockFlagWrong.mockResolvedValue(undefined)
    render(<WrongFlag target="day" flagRef="2026-10-15#0" excerpt="Long ride" />)

    await userEvent.click(screen.getByRole('button', { name: 'This is wrong' }))
    await userEvent.click(screen.getByRole('button', { name: 'Send' }))

    expect(mockFlagWrong).toHaveBeenCalledWith('tok', expect.objectContaining({ note: undefined }))
  })

  it('keeps the note and says why when sending fails', async () => {
    mockFlagWrong.mockRejectedValue(new Error('Could not reach the server. Check your connection and try again.'))
    render(<WrongFlag target="day" flagRef="2026-10-15#0" excerpt="Long ride" />)

    await userEvent.click(screen.getByRole('button', { name: 'This is wrong' }))
    await userEvent.type(screen.getByRole('textbox'), 'Too long')
    await userEvent.click(screen.getByRole('button', { name: 'Send' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Could not reach the server.')
    expect(screen.getByRole('textbox')).toHaveValue('Too long')
  })

  it('goes back to one quiet link on cancel', async () => {
    render(<WrongFlag target="day" flagRef="2026-10-15#0" excerpt="Long ride" />)

    await userEvent.click(screen.getByRole('button', { name: 'This is wrong' }))
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    expect(mockFlagWrong).not.toHaveBeenCalled()
  })

  it('is not offered without a session', () => {
    useAppStore.setState({ authToken: null })
    const { container } = render(<WrongFlag target="day" flagRef="2026-10-15#0" excerpt="Long ride" />)

    expect(container).toBeEmptyDOMElement()
  })
})
