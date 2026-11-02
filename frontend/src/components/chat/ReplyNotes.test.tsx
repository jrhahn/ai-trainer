import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { useAppStore } from '../../store/useAppStore'
import ReplyNotes from './ReplyNotes'

const NOTE = { kind: 'unresolved_dates', text: 'I could not update 2026-04-10.' }
const LANGUAGE = useAppStore.getState().uiLanguage

describe('ReplyNotes', () => {
  afterEach(() => useAppStore.setState({ uiLanguage: LANGUAGE }))

  it('shows the notes under one heading that says they are the app\'s (ai-trainer-ops#338 A10)', () => {
    useAppStore.setState({ uiLanguage: 'en' })
    render(<ReplyNotes notes={[NOTE, { kind: 'location', text: 'I set your usual training location.' }]} />)

    expect(screen.getAllByText('From the app')).toHaveLength(1)
    expect(screen.getAllByRole('listitem')).toHaveLength(2)
    expect(screen.getByText(/could not update 2026-04-10/)).toBeInTheDocument()
  })

  it('heads the notes in German on a German screen', () => {
    useAppStore.setState({ uiLanguage: 'de' })
    render(<ReplyNotes notes={[NOTE]} />)

    expect(screen.getByText('Von der App')).toBeInTheDocument()
  })

  it('shows nothing without a note', () => {
    const { container } = render(<ReplyNotes notes={[]} />)

    expect(container).toBeEmptyDOMElement()
  })
})
