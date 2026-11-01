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
    expect(await screen.findByRole('status')).toHaveTextContent('Thanks. We see what you flagged, and your note.')
  })

  it('sends no note when none was written', async () => {
    mockFlagWrong.mockResolvedValue(undefined)
    render(<WrongFlag target="day" flagRef="2026-10-15#0" excerpt="Long ride" />)

    await userEvent.click(screen.getByRole('button', { name: 'This is wrong' }))
    await userEvent.click(screen.getByRole('button', { name: 'Send' }))

    expect(mockFlagWrong).toHaveBeenCalledWith('tok', expect.objectContaining({ note: undefined }))
    // The thanks mentions a note only when one was written (ai-trainer-ops#131).
    expect(await screen.findByRole('status')).toHaveTextContent('Thanks. We see what you flagged.')
    expect(screen.getByRole('status')).not.toHaveTextContent('note')
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

describe('WrongFlag: telling the coach too (ai-trainer-ops#338 F10)', () => {
  beforeEach(() => {
    mockFlagWrong.mockResolvedValue(undefined)
    useAppStore.setState({ pendingCoachMessage: null })
  })

  it('gives a flagged day with a note to the coach, who can change the plan', async () => {
    render(<WrongFlag target="day" flagRef="2026-10-15#0" excerpt={'Long ride\n3 h\nSteady.'} />)

    await userEvent.click(screen.getByRole('button', { name: 'This is wrong' }))
    await userEvent.type(screen.getByRole('textbox'), 'I am away that day.')
    await userEvent.click(screen.getByRole('button', { name: 'Send' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Tell your coach too' }))

    expect(useAppStore.getState().pendingCoachMessage).toBe('About Long ride on 2026-10-15: I am away that day.')
    expect(screen.getByRole('status')).toHaveTextContent('Thanks. Your note went to your coach as well.')
  })

  it('quotes the start of a flagged reply', async () => {
    render(<WrongFlag target="reply" flagRef="2026-10-14T10:00:00Z" excerpt={`Ride long on Sunday ${'and more '.repeat(20)}`} />)

    await userEvent.click(screen.getByRole('button', { name: 'This is wrong' }))
    await userEvent.type(screen.getByRole('textbox'), 'The race is Saturday.')
    await userEvent.click(screen.getByRole('button', { name: 'Send' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Tell your coach too' }))

    const sent = useAppStore.getState().pendingCoachMessage ?? ''
    expect(sent).toMatch(/^About your answer “Ride long on Sunday and more .* …”: The race is Saturday\.$/)
    expect(sent.length).toBeLessThan(140)
  })

  it('offers nothing to tell without a note', async () => {
    render(<WrongFlag target="day" flagRef="2026-10-15#0" excerpt="Long ride" />)

    await userEvent.click(screen.getByRole('button', { name: 'This is wrong' }))
    await userEvent.click(screen.getByRole('button', { name: 'Send' }))

    await screen.findByRole('status')
    expect(screen.queryByRole('button', { name: 'Tell your coach too' })).not.toBeInTheDocument()
    expect(useAppStore.getState().pendingCoachMessage).toBeNull()
  })
})
