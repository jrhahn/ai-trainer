import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import FitFileUpload from './FitFileUpload'
import type { FitBulkUploadResponse } from '../services/user'

const mockUploadFitFiles = vi.hoisted(() => vi.fn())
vi.mock('../services/user', () => ({ uploadFitFiles: mockUploadFitFiles }))

vi.mock('../store/useAppStore', () => ({
  useAppStore: (selector: (state: { authToken: string }) => unknown) =>
    selector({ authToken: 'test-token' }),
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
})

describe('FitFileUpload', () => {
  it('renders the upload prompt', () => {
    render(<FitFileUpload />)
    expect(screen.getByText('Choose .fit files')).toBeInTheDocument()
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
})
