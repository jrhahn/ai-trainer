import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import PowerCurvePage from './PowerCurvePage'
import { useAppStore } from '../store/useAppStore'

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

  it('posts the file with no token and draws the fit', async () => {
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
    const headers = init.headers as Record<string, string>
    // No account, so no token goes with it (ai-trainer-ops#25). No JSON header
    // either: the browser writes the multipart one (ai-trainer-ops#276).
    expect(headers.Authorization).toBeUndefined()
    expect(headers['Content-Type']).toBeUndefined()
    expect(init.body).toBeInstanceOf(FormData)
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

describe('PowerCurvePage in German (ai-trainer-ops#104)', () => {
  beforeEach(() => vi.restoreAllMocks())

  it('explains a ride without a fit, and a refusal, in German', async () => {
    useAppStore.setState({ uiLanguage: 'de' })
    try {
      vi.spyOn(globalThis, 'fetch')
        .mockResolvedValueOnce(new Response(JSON.stringify({ ...curve, model: null, curve: [] }), { status: 200 }))
        .mockResolvedValueOnce(
          new Response(JSON.stringify({ detail: 'This file has no power data. The curve needs a power meter.' }), { status: 422 }),
        )
      render(<MemoryRouter><PowerCurvePage /></MemoryRouter>)

      expect(screen.getByRole('heading', { name: 'Deine Leistungs-Dauer-Kurve' })).toBeInTheDocument()
      await userEvent.upload(screen.getByLabelText('.fit-Datei wählen'), new File(['x'], 'ride.fit'))
      expect(await screen.findByText(/Keine Critical-Power-Anpassung/)).toBeInTheDocument()

      await userEvent.upload(screen.getByLabelText('.fit-Datei wählen'), new File(['y'], 'flat.fit'))
      expect(
        await screen.findByText('Diese Datei hat keine Leistungsdaten. Die Kurve braucht einen Leistungsmesser.'),
      ).toBeInTheDocument()
    } finally {
      useAppStore.setState({ uiLanguage: 'auto' })
    }
  })
})
