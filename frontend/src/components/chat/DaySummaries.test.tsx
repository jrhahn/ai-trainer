import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { useAppStore } from '../../store/useAppStore'
import DaySummaries from './DaySummaries'

const LANGUAGE = useAppStore.getState().uiLanguage

describe('DaySummaries (ai-trainer-ops#266)', () => {
  afterEach(() => useAppStore.setState({ uiLanguage: LANGUAGE }))

  it("names each changed day in the interface's words and gives the coach's sentence", () => {
    useAppStore.setState({ uiLanguage: 'en' })
    render(
      <DaySummaries
        items={[
          { date: '2026-10-29', summary: 'An easy hour instead of the intervals.' },
          { date: '2026-10-31', summary: 'The long ride moves here.' },
        ]}
      />,
    )

    expect(screen.getByText('Thu, Oct 29:')).toBeInTheDocument()
    expect(screen.getByText(/An easy hour instead of the intervals\./)).toBeInTheDocument()
    expect(screen.getByText('Sat, Oct 31:')).toBeInTheDocument()
  })

  it('dates the day in German on a German screen, and leaves the sentence as the coach wrote it', () => {
    useAppStore.setState({ uiLanguage: 'de' })
    render(<DaySummaries items={[{ date: '2026-10-29', summary: 'Locker eine Stunde statt der Intervalle.' }]} />)

    expect(screen.getByText('Do., 29. Okt.:')).toBeInTheDocument()
    expect(screen.getByText(/Locker eine Stunde statt der Intervalle\./)).toBeInTheDocument()
  })

  it('shows nothing without a changed day', () => {
    const { container } = render(<DaySummaries items={[]} />)

    expect(container).toBeEmptyDOMElement()
  })
})
