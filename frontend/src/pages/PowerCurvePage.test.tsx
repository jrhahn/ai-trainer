import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import PowerCurvePage from './PowerCurvePage'

const curve = {
  durationSeconds: 5400,
  points: [
    { seconds: 5, watts: 900 }, { seconds: 120, watts: 420 },
    { seconds: 300, watts: 320 }, { seconds: 1200, watts: 265 },
  ],
  model: { cp: 252, cpLow: 240, cpHigh: 264, wPrime: 19800, wPrimeLow: 15000, wPrimeHigh: 24600, pointsUsed: 3 },
  curve: [
    { seconds: 120, watts: 417, low: 400, high: 434 },
    { seconds: 1200, watts: 268, low: 255, high: 281 },
  ],
}

describe('PowerCurvePage', () => {
  beforeEach(() => vi.restoreAllMocks())

  it('posts the file without credentials and draws the fit', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(curve), { status: 200 }),
    )
    render(<MemoryRouter><PowerCurvePage /></MemoryRouter>)

    await userEvent.upload(
      screen.getByLabelText(/choose a .fit file/i),
      new File(['x'], 'ride.fit'),
    )

    expect(await screen.findByText(/CP 252 W \(240–264\)/)).toBeInTheDocument()
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toMatch(/\/tools\/power-curve$/)
    expect(init.headers).toBeUndefined()
    expect(init.credentials).toBeUndefined()
  })

  it('says why a file was refused', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ detail: 'This file has no power data.' }), { status: 422 }),
    )
    render(<MemoryRouter><PowerCurvePage /></MemoryRouter>)

    await userEvent.upload(screen.getByLabelText(/choose a .fit file/i), new File(['x'], 'run.fit'))

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('no power data'))
  })
})
