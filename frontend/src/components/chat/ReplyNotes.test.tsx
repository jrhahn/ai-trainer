import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { useAppStore } from '../../store/useAppStore'
import ReplyNotes from './ReplyNotes'

const NOTE = { kind: 'unresolved_dates', text: 'I could not update 2026-04-10.' }
const LANGUAGE = useAppStore.getState().uiLanguage

describe('ReplyNotes', () => {
  afterEach(() => useAppStore.setState({ uiLanguage: LANGUAGE }))

  it('shows each note under its label', () => {
    useAppStore.setState({ uiLanguage: 'en' })
    render(<ReplyNotes notes={[NOTE, { kind: 'location', text: 'I set your usual training location.' }]} />)

    expect(screen.getAllByText('Note:')).toHaveLength(2)
    expect(screen.getByText(/could not update 2026-04-10/)).toBeInTheDocument()
  })

  it('labels a note in German on a German screen', () => {
    useAppStore.setState({ uiLanguage: 'de' })
    render(<ReplyNotes notes={[NOTE]} />)

    expect(screen.getByText('Hinweis:')).toBeInTheDocument()
  })

  it('shows nothing without a note', () => {
    const { container } = render(<ReplyNotes notes={[]} />)

    expect(container).toBeEmptyDOMElement()
  })
})
