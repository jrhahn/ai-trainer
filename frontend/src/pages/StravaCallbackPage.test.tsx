import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import StravaCallbackPage from './StravaCallbackPage'
import { useAppStore } from '../store/useAppStore'

const mockNavigate = vi.fn()
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>()
  return { ...actual, useNavigate: () => mockNavigate }
})

const mockApiFetch = vi.fn()
vi.mock('../services/api', () => ({
  apiFetch: (...args: unknown[]) => mockApiFetch(...args),
  API_BASE: 'http://localhost:8000/api/v1',
}))

function setup(search = '') {
  Object.defineProperty(window, 'location', {
    value: { ...window.location, search },
    writable: true,
  })
  useAppStore.setState({ authToken: 'tok-123' })
  return render(
    <MemoryRouter>
      <StravaCallbackPage />
    </MemoryRouter>
  )
}

beforeEach(() => {
  useAppStore.getState().resetAll()
  vi.clearAllMocks()
  mockNavigate.mockReset()

  // Default: POST starts the import, GET returns done with 42 rides
  mockApiFetch.mockImplementation((path: string) => {
    if (path === '/users/me') {
      return Promise.resolve({
        id: 'user-1',
        email: 'rider@example.com',
        name: 'Test Rider',
        isOnboarded: false,
        stravaAnalysisComplete: false,
        lastStravaActivityId: null,
        followsTrainingPlan: false,
        aiProvider: 'openai',
        stravaConnection: { athleteId: 42, athleteName: 'Alex Rider' },
        riderAssessment: null,
      })
    }
    if (path === '/strava/import-progress') {
      return Promise.resolve({
        jobId: 'job-1',
        status: 'done',
        total: 42,
        processed: 42,
        imported: 42,
        skipped: 0,
        failedActivities: [],
        error: '',
      })
    }
    return Promise.resolve({ status: 'started', jobId: 'job-1' })
  })
})

describe('StravaCallbackPage', () => {
  it('shows an error when no success or error param is present', () => {
    setup('')
    expect(screen.getByText('Import Failed')).toBeInTheDocument()
    expect(screen.getByText(/No success confirmation received/)).toBeInTheDocument()
  })

  it('shows an error when the error query param is present', () => {
    setup('?error=access_denied')
    expect(screen.getByText('Import Failed')).toBeInTheDocument()
    expect(screen.getByText('access_denied')).toBeInTheDocument()
  })

  it('shows the importing step while the API call is in flight', async () => {
    // Progress stays at 'running' — component stays in importing step
    mockApiFetch.mockImplementation((path: string) => {
      if (path === '/users/me') {
        return Promise.resolve({
          id: 'user-1',
          email: 'rider@example.com',
          name: 'Test Rider',
          isOnboarded: false,
          stravaAnalysisComplete: false,
          lastStravaActivityId: null,
          followsTrainingPlan: false,
          aiProvider: 'openai',
          stravaConnection: { athleteId: 42, athleteName: 'Alex Rider' },
          riderAssessment: null,
        })
      }
      if (path === '/strava/import-progress') {
        return Promise.resolve({
          status: 'running',
          total: 50,
          processed: 10,
          imported: 0,
          skipped: 0,
          failedActivities: [],
          error: '',
        })
      }
      return new Promise(() => {}) // POST never resolves
    })
    setup('?success=1')

    await waitFor(() => {
      expect(screen.getByText(/Importing ride history/i)).toBeInTheDocument()
    })
  })

  it('shows done with ride count after successful import', async () => {
    setup('?success=1')

    await waitFor(() => {
      expect(screen.getByText(/Import complete/i)).toBeInTheDocument()
      expect(screen.getByText(/42 rides imported/i)).toBeInTheDocument()
      expect(screen.getByText(/All processed activities imported successfully/i)).toBeInTheDocument()
    })

    await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith('/'), { timeout: 3000 })
  })

  it('reloads current user before starting the import after OAuth success', async () => {
    setup('?success=1')

    await waitFor(() => {
      expect(mockApiFetch).toHaveBeenCalledWith('/users/me', { token: 'tok-123' })
      expect(mockApiFetch).toHaveBeenCalledWith(
        expect.stringContaining('/strava/import-history'),
        expect.objectContaining({ method: 'POST' }),
      )
    })
    const userFetchOrder = mockApiFetch.mock.invocationCallOrder.find((_, idx) => {
      const call = mockApiFetch.mock.calls[idx]
      return call[0] === '/users/me'
    }) ?? Number.MAX_SAFE_INTEGER
    const importOrder =
      mockApiFetch.mock.invocationCallOrder.find((_, idx) => {
        const call = mockApiFetch.mock.calls[idx]
        return typeof call[0] === 'string' && call[0].includes('/strava/import-history')
      }) ?? Number.MAX_SAFE_INTEGER

    expect(userFetchOrder).toBeLessThan(importOrder)
    expect(useAppStore.getState().stravaConnection?.athleteName).toBe('Alex Rider')
  })

  it('shows skipped activity details without treating them as fatal', async () => {
    mockApiFetch.mockImplementation((path: string) => {
      if (path === '/strava/import-progress') {
        return Promise.resolve({
          jobId: 'job-1',
          status: 'done',
          total: 2,
          processed: 2,
          imported: 1,
          skipped: 1,
          failedActivities: [
            {
              activityId: 222,
              activityName: 'Broken ride',
              activityDate: '2026-04-02',
              reason: 'Stream download failed',
            },
          ],
          error: '',
        })
      }
      return Promise.resolve({ status: 'started', jobId: 'job-1' })
    })
    setup('?success=1')

    await waitFor(() => {
      expect(screen.getByText(/Import complete/i)).toBeInTheDocument()
      expect(screen.getByText(/1 ride imported, 1 skipped/i)).toBeInTheDocument()
      expect(screen.getByText(/Broken ride/i)).toBeInTheDocument()
      expect(screen.getByText(/Stream download failed/i)).toBeInTheDocument()
    })
    expect(mockNavigate).not.toHaveBeenCalledWith('/')
  })

  it('shows fatal import errors instead of a success report', async () => {
    mockApiFetch.mockImplementation((path: string) => {
      if (path === '/strava/import-progress') {
        return Promise.resolve({
          status: 'error',
          total: 0,
          processed: 0,
          imported: 0,
          skipped: 0,
          failedActivities: [],
          error: 'Strava list error 500',
        })
      }
      return Promise.resolve({ status: 'started', jobId: 'job-1' })
    })
    setup('?success=1')

    await waitFor(() => {
      expect(screen.getByText(/Import Failed/i)).toBeInTheDocument()
      expect(screen.getByText(/Strava list error 500/i)).toBeInTheDocument()
    })
  })

  it('navigates to /settings when the Go to Settings button is clicked on the error screen', async () => {
    setup('?error=access_denied')

    await userEvent.click(screen.getByRole('button', { name: /go to settings/i }))

    expect(mockNavigate).toHaveBeenCalledWith('/settings')
  })
})
