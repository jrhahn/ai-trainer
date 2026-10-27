import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import TimelineEventView from './TimelineEventView'
import type { TimelineEvent } from '../../utils/coachTimeline'

function hypothesisEvent(overrides: Partial<TimelineEvent> = {}): TimelineEvent {
  return {
    id: 'hypothesis-1',
    kind: 'hypothesis',
    timestamp: '2026-10-27T06:00:00Z',
    title: 'Coach hypothesis',
    body: 'Threshold holds in the heat.',
    ...overrides,
  }
}

describe('TimelineEventView', () => {
  it('dims a recommendation whose text is still being translated, and says so (ai-trainer-ops#258)', () => {
    render(<TimelineEventView event={hypothesisEvent({ translating: true })} />)

    expect(screen.getByText('Threshold holds in the heat.')).toHaveClass('opacity-60')
    expect(screen.getByText('Translation on its way …')).toBeInTheDocument()
  })

  it('shows a recommendation with its text in place as before', () => {
    render(<TimelineEventView event={hypothesisEvent({ translating: false })} />)

    expect(screen.getByText('Threshold holds in the heat.')).not.toHaveClass('opacity-60')
    expect(screen.queryByText('Translation on its way …')).toBeNull()
  })
})
