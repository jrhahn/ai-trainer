import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import FitFileUpload, { MAX_FILES_PER_UPLOAD } from './FitFileUpload'
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
    expect(screen.getByText('Choose or drop .fit, .gpx, .tcx or .zip files')).toBeInTheDocument()
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
    expect(mockUploadFitFiles).toHaveBeenCalledWith('test-token', [expect.any(File)], true)
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

  it('takes GPX and TCX as well as .fit and .zip (ai-trainer-ops#311)', () => {
    const { container } = render(<FitFileUpload />)
    const input = container.querySelector('input[type="file"]') as HTMLInputElement
    expect(input.accept).toBe('.fit,.zip,.gpx,.tcx')
    expect(screen.getByText(/or a \.gpx or \.tcx export/)).toBeInTheDocument()
  })

  it('says under an imported file what its load was estimated from (ai-trainer-ops#311)', async () => {
    mockUploadFitFiles.mockResolvedValue(
      response({
        total: 2,
        imported: 2,
        files: [
          { filename: 'morning.gpx', status: 'imported', message: 'Imported GPX activity', sportType: 'cycling', durationMinutes: 30, averageHeartRate: 140, loadSource: 'heart_rate' },
          { filename: 'power.fit', status: 'imported', message: 'Imported FIT activity', sportType: 'cycling', durationMinutes: 60, averagePower: 220, loadSource: 'power' },
        ],
      })
    )
    const { container } = render(<FitFileUpload />)

    await userEvent.upload(container.querySelector('input[type="file"]') as HTMLInputElement, makeFile('morning.gpx'))

    expect(await screen.findByText('Load estimated from your heart rate: the file has no power.')).toBeInTheDocument()
    // A measured load needs no note: only the one estimate is said.
    expect(screen.getAllByText(/^Load estimated/)).toHaveLength(1)
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
    const target = screen.getByText('Choose or drop .fit, .gpx, .tcx or .zip files')

    fireEvent.dragOver(target, { dataTransfer: { files: [] } })
    fireEvent.dragLeave(target)
    fireEvent.drop(target, { dataTransfer: { files: [makeFile('export.zip')] } })

    await waitFor(() => expect(mockUploadFitFiles).toHaveBeenCalledWith('test-token', [expect.any(File)], true))
    expect(mockUploadFitFiles.mock.calls[0][1][0].name).toBe('export.zip')
  })

  it('ignores a drop with no files', () => {
    render(<FitFileUpload />)
    fireEvent.drop(screen.getByText('Choose or drop .fit, .gpx, .tcx or .zip files'), { dataTransfer: { files: [] } })
    expect(mockUploadFitFiles).not.toHaveBeenCalled()
  })

  it('links an imported file to the day it happened', async () => {
    mockUploadFitFiles.mockResolvedValue(
      response({ files: [{ filename: 'ride.fit', status: 'imported', message: 'ok', activityDate: '2026-10-07' }] })
    )
    const { container } = render(
      <MemoryRouter>
        <FitFileUpload />
      </MemoryRouter>
    )
    await userEvent.upload(container.querySelector('input[type="file"]') as HTMLInputElement, makeFile())

    expect(await screen.findByRole('link', { name: 'Open day' })).toHaveAttribute('href', '/workout/2026-10-07')
  })

  it('offers no day for a file that failed', async () => {
    mockUploadFitFiles.mockResolvedValue(
      response({ imported: 0, failed: 1, files: [{ filename: 'x.fit', status: 'failed', message: 'bad', activityDate: '2026-10-07' }] })
    )
    const { container } = render(
      <MemoryRouter>
        <FitFileUpload />
      </MemoryRouter>
    )
    await userEvent.upload(container.querySelector('input[type="file"]') as HTMLInputElement, makeFile())

    await screen.findByText('0 imported')
    expect(screen.queryByRole('link', { name: 'Open day' })).not.toBeInTheDocument()
  })

  it('sends a large selection in parts and adds the parts up (ai-trainer-ops#157)', async () => {
    const files = Array.from({ length: MAX_FILES_PER_UPLOAD * 2 + 3 }, (_, i) => makeFile(`r${i}.fit`))
    mockUploadFitFiles.mockImplementation(async (_token: string, part: File[]) =>
      response({
        total: part.length,
        imported: part.length - 1,
        skipped: 1,
        files: part.map((f, i) => ({ filename: f.name, status: i === 0 ? 'skipped' : 'imported', message: '' })),
      }),
    )
    const { container } = render(<FitFileUpload />)

    await userEvent.upload(container.querySelector('input[type="file"]') as HTMLInputElement, files)

    expect(await screen.findByText(`${files.length - 3} imported`)).toBeInTheDocument()
    expect(mockUploadFitFiles.mock.calls.map((call) => call[1].length)).toEqual([
      MAX_FILES_PER_UPLOAD,
      MAX_FILES_PER_UPLOAD,
      3,
    ])
    // Only the last part learns, once for the selection (ai-trainer-ops#301).
    expect(mockUploadFitFiles.mock.calls.map((call) => call[2])).toEqual([false, false, true])
    expect(screen.getByText('3 skipped')).toBeInTheDocument()
    expect(screen.getByText('r52.fit')).toBeInTheDocument()
  })

  it('lets a selection of exactly one part learn (ai-trainer-ops#301)', async () => {
    const files = Array.from({ length: MAX_FILES_PER_UPLOAD }, (_, i) => makeFile(`r${i}.fit`))
    mockUploadFitFiles.mockResolvedValue(response())
    const { container } = render(<FitFileUpload />)

    await userEvent.upload(container.querySelector('input[type="file"]') as HTMLInputElement, files)

    await waitFor(() => expect(mockUploadFitFiles).toHaveBeenCalledTimes(1))
    expect(mockUploadFitFiles.mock.calls[0][2]).toBe(true)
  })

  it('counts the files while a large selection goes up', async () => {
    let release: () => void = () => {}
    mockUploadFitFiles
      .mockResolvedValueOnce(response())
      .mockImplementationOnce(() => new Promise((resolve) => { release = () => resolve(response()) }))
    const files = Array.from({ length: MAX_FILES_PER_UPLOAD + 1 }, (_, i) => makeFile(`r${i}.fit`))
    const { container } = render(<FitFileUpload />)

    await userEvent.upload(container.querySelector('input[type="file"]') as HTMLInputElement, files)

    expect(await screen.findByText(`Uploading… ${MAX_FILES_PER_UPLOAD} of ${files.length} files`)).toBeInTheDocument()
    release()
    expect(await screen.findByText('2 imported')).toBeInTheDocument()
  })

  it('keeps what earlier parts imported when a later part fails', async () => {
    mockUploadFitFiles.mockResolvedValueOnce(response({ imported: 20 })).mockRejectedValueOnce(new Error('Server gone'))
    const files = Array.from({ length: MAX_FILES_PER_UPLOAD + 1 }, (_, i) => makeFile(`r${i}.fit`))
    const { container } = render(<FitFileUpload />)

    await userEvent.upload(container.querySelector('input[type="file"]') as HTMLInputElement, files)

    expect(await screen.findByText('Server gone')).toBeInTheDocument()
    expect(screen.getByText('20 imported before the error.')).toBeInTheDocument()
    // What arrived is on the dashboard as well (review of #978).
    await waitFor(() => expect(mockSetRides).toHaveBeenCalled())
  })
})
