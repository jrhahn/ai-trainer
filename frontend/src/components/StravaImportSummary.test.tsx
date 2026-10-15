import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import StravaImportSummary from './StravaImportSummary'
import type { ImportProgress } from '../services/strava'

function progress(failures: number): ImportProgress {
  return {
    status: 'done',
    total: 20,
    processed: 20,
    imported: 20 - failures,
    skipped: failures,
    error: '',
    failedActivities: Array.from({ length: failures }, (_, i) => ({
      activityId: i + 1,
      // The first one has no name, as an activity can.
      activityName: i === 0 ? '' : `Ride ${i + 1}`,
      activityDate: '2026-10-01',
      reason: 'no power data',
    })),
  } as ImportProgress
}

describe('StravaImportSummary', () => {
  it('names an unnamed activity and counts the failures it does not list', () => {
    render(<StravaImportSummary progress={progress(12)} />)

    expect(screen.getByText(/Unnamed activity/)).toBeInTheDocument()
    expect(screen.getByText('2 more skipped activities not shown.')).toBeInTheDocument()
  })

  it('says one more in the singular', () => {
    render(<StravaImportSummary progress={progress(11)} />)

    expect(screen.getByText('1 more skipped activity not shown.')).toBeInTheDocument()
  })

  it('reports a clean import', () => {
    render(<StravaImportSummary progress={progress(0)} />)

    expect(screen.getByText('All processed activities imported successfully.')).toBeInTheDocument()
  })
})
