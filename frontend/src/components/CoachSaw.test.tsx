import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import CoachSaw from './CoachSaw'

const ITEM = {
  key: 'priors',
  title: 'Planning priors',
  text: 'The 7 days from 2026-10-14 plan 7.0 h; the athlete said they can give 3 h a week.',
  source: {
    path: 'backend/services/plan_priors.py',
    line: 412,
    symbol: 'find_prior_departures',
    ref: 'deadbeef',
  },
}

describe('CoachSaw', () => {
  it('lists each block with a link to its code at the deployed commit', async () => {
    render(<CoachSaw items={[ITEM]} />)
    await userEvent.click(screen.getByText('What the coach saw'))

    expect(screen.getByText(/can give 3 h a week/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'find_prior_departures' })).toHaveAttribute(
      'href',
      'https://github.com/jrhahn/ai-trainer/blob/deadbeef/backend/services/plan_priors.py#L412',
    )
  })

  it('shows nothing when the coach was given no block', () => {
    const { container } = render(<CoachSaw items={[]} />)
    expect(container).toBeEmptyDOMElement()
  })
})
