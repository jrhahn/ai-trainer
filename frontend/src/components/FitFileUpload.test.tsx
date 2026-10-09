import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import FitFileUpload from './FitFileUpload'
import type { FitBulkUploadResponse } from '../services/user'

const { mockUploadFitFiles, mockFetchRides, mockFetchMetrics, mockSetRides, mockSetMetrics } =
  vi.hoisted(() => ({
    mockUploadFitFiles: vi.fn(),
    mockFetchRides: vi.fn(),
    mockFetchMetrics: vi.fn(),
    mockSetRides: vi.fn(),
    mockSetMetrics: vi.fn(),
  }))
vi.mock('../services/user', () => ({
  uploadFitFiles: mockUploadFitFiles,
  fetchRideMetricsHistory: mockFetchRides,
  fetchMetricsHistory: mockFetchMetrics,
}))

vi.mock('../store/useAppStore', () => ({
  useAppStore: (selector: (state: Record<string, unknown>) => unknown) =>
    selector({
      authToken: 'test-token',
      setRideMetricsHistory: mockSetRides,
      setMetricsHistory: mockSetMetrics,
    }),
}))

function response(overrides: Partial<FitBulkUploadResponse> = {}): FitBulkUploadResponse {
  return {
    status: 'ok',
    total: 1,
    imported: 1,
    skipped: 0,
    failed: 0,
    files: [
      {
        filename: 'ride.fit',
        status: 'imported',
        message: 'ok',
        sportType: 'cycling',
        durationMinutes: 60,
        averagePower: 220,
      },
    ],
    ...overrides,
  }
}

function makeFile(name = 'ride.fit') {
  return new File(['fit-bytes'], name, { type: 'application/octet-stream' })
}

beforeEach(() => {
  vi.clearAllMocks()
  mockFetchRides.mockResolvedValue([{ stravaActivityId: 1 }])
  mockFetchMetrics.mockResolvedValue([])
})

describe('FitFileUpload', () => {
  it('renders the upload prompt', () => {
    render(<FitFileUpload />)
    expect(screen.getByText('Choose or drop .fit / .zip files')).toBeInTheDocument()
  })

  it('drops the card chrome when embedded in another panel', () => {
    const { container } = render(<FitFileUpload embedded />)
    expect(container.firstElementChild).toHaveAttribute('class', '')
  })

  it('uploads selected files and shows the per-file result summary', async () => {
    mockUploadFitFiles.mockResolvedValue(response())
    const { container } = render(<FitFileUpload />)

    const input = container.querySelector('input[type="file"]') as HTMLInputElement
    await userEvent.upload(input, makeFile())

    await waitFor(() => expect(mockUploadFitFiles).toHaveBeenCalledTimes(1))
    expect(mockUploadFitFiles).toHaveBeenCalledWith('test-token', [expect.any(File)])
    expect(await screen.findByText('1 imported')).toBeInTheDocument()
    expect(screen.getByText('ride.fit')).toBeInTheDocument()
    expect(screen.getByText(/cycling · 60 min · 220W/)).toBeInTheDocument()
  })

  it('shows an error banner when the upload fails', async () => {
    mockUploadFitFiles.mockRejectedValue(new Error('Bad file'))
    const { container } = render(<FitFileUpload />)

    const input = container.querySelector('input[type="file"]') as HTMLInputElement
    await userEvent.upload(input, makeFile())

    expect(await screen.findByText('Bad file')).toBeInTheDocument()
  })

  it('renders skipped and failed file rows with their messages', async () => {
    mockUploadFitFiles.mockResolvedValue(
      response({
        imported: 0,
        skipped: 1,
        failed: 1,
        files: [
          { filename: 'dupe.fit', status: 'skipped', message: 'Already imported' },
          { filename: 'broken.fit', status: 'failed', message: 'Corrupt data' },
        ],
      })
    )
    const { container } = render(<FitFileUpload />)

    const input = container.querySelector('input[type="file"]') as HTMLInputElement
    await userEvent.upload(input, makeFile())

    expect(await screen.findByText('Already imported')).toBeInTheDocument()
    expect(screen.getByText('Corrupt data')).toBeInTheDocument()
  })

  // ---------------------------------------------------------------------------
  // ai-trainer-ops#48
  // ---------------------------------------------------------------------------

  it('lets GPX and TCX be picked, so the server can say what to do instead', () => {
    const { container } = render(<FitFileUpload />)
    const input = container.querySelector('input[type="file"]') as HTMLInputElement
    expect(input.accept).toBe('.fit,.zip,.gpx,.tcx')
  })

  it('refreshes activities and load after an import', async () => {
    mockUploadFitFiles.mockResolvedValue(response())
    const { container } = render(<FitFileUpload />)
    await userEvent.upload(container.querySelector('input[type="file"]') as HTMLInputElement, makeFile())

    await waitFor(() => expect(mockSetRides).toHaveBeenCalledWith([{ stravaActivityId: 1 }]))
    expect(mockSetMetrics).toHaveBeenCalledWith([])
  })

  it('refreshes nothing when nothing was imported', async () => {
    mockUploadFitFiles.mockResolvedValue(response({ imported: 0, failed: 1 }))
    const { container } = render(<FitFileUpload />)
    await userEvent.upload(container.querySelector('input[type="file"]') as HTMLInputElement, makeFile())

    await screen.findByText('0 imported')
    expect(mockFetchRides).not.toHaveBeenCalled()
  })

  it('keeps the result when the refresh after it fails', async () => {
    mockUploadFitFiles.mockResolvedValue(response())
    mockFetchRides.mockRejectedValue(new Error('offline'))
    const { container } = render(<FitFileUpload />)
    await userEvent.upload(container.querySelector('input[type="file"]') as HTMLInputElement, makeFile())

    expect(await screen.findByText('1 imported')).toBeInTheDocument()
    expect(mockSetRides).not.toHaveBeenCalled()
  })

  it('uploads files dropped onto it', async () => {
    mockUploadFitFiles.mockResolvedValue(response())
    render(<FitFileUpload />)
    const target = screen.getByText('Choose or drop .fit / .zip files')

    fireEvent.dragOver(target, { dataTransfer: { files: [] } })
    fireEvent.dragLeave(target)
    fireEvent.drop(target, { dataTransfer: { files: [makeFile('export.zip')] } })

    await waitFor(() => expect(mockUploadFitFiles).toHaveBeenCalledWith('test-token', [expect.any(File)]))
    expect(mockUploadFitFiles.mock.calls[0][1][0].name).toBe('export.zip')
  })

  it('ignores a drop with no files', () => {
    render(<FitFileUpload />)
    fireEvent.drop(screen.getByText('Choose or drop .fit / .zip files'), { dataTransfer: { files: [] } })
    expect(mockUploadFitFiles).not.toHaveBeenCalled()
  })
})

