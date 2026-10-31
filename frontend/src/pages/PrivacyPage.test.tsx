import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import PrivacyPage from './PrivacyPage'
import { useAppStore } from '../store/useAppStore'

const mockApiFetch = vi.hoisted(() => vi.fn())
vi.mock('../services/api', () => ({ apiFetch: mockApiFetch }))

function setup() {
  return render(
    <MemoryRouter>
      <PrivacyPage />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  mockApiFetch.mockResolvedValue({ operatorName: 'Jane Doe', contactEmail: 'hello@example.com' })
})

afterEach(() => {
  useAppStore.setState({ uiLanguage: 'auto' })
})

describe('PrivacyPage (ai-trainer-ops#2, #5)', () => {
  it('names the controller and the contact from the server, and every recipient', async () => {
    setup()

    expect(await screen.findByText(/The controller is Jane Doe/)).toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: 'hello@example.com' })[0]).toHaveAttribute(
      'href',
      'mailto:hello@example.com',
    )
    expect(mockApiFetch).toHaveBeenCalledWith('/auth/legal')
    for (const recipient of ['Hetzner Online GmbH', 'Google (Gemini API)', 'OpenAI', 'Scaleway', 'Open-Meteo', 'intervals.icu and Strava']) {
      expect(screen.getByText(recipient)).toBeInTheDocument()
    }
    expect(screen.getByRole('heading', { name: 'Health data' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'A note on training' }).closest('section')).toHaveAttribute('id', 'training')
  })

  it('gives the postal address after the controller in one line, in both languages (ai-trainer-ops#1)', async () => {
    mockApiFetch.mockResolvedValue({
      operatorName: 'Jane Doe',
      operatorAddress: 'c/o Box#12, Main Street 1, 12345 Town',
      contactEmail: 'hello@example.com',
    })
    const { unmount } = setup()

    expect(await screen.findByText(/The controller is Jane Doe, c\/o Box#12, Main Street 1, 12345 Town\. Questions/)).toBeInTheDocument()
    unmount()

    useAppStore.setState({ uiLanguage: 'de' })
    setup()
    expect(await screen.findByText(/Verantwortlich ist Jane Doe, c\/o Box#12, Main Street 1, 12345 Town\. Fragen/)).toBeInTheDocument()
  })

  it('leaves the address out while none is set, without a comma', async () => {
    setup()

    expect(await screen.findByText(/The controller is Jane Doe\. Questions/)).toBeInTheDocument()
  })

  it('says what is not set yet instead of guessing', async () => {
    mockApiFetch.mockResolvedValue({ operatorName: null, contactEmail: null })
    setup()

    expect(await screen.findAllByText('(not set yet)')).toHaveLength(3)
    expect(screen.queryByRole('link', { name: /@/ })).not.toBeInTheDocument()
  })

  it('is German in a German interface', async () => {
    useAppStore.setState({ uiLanguage: 'de' })
    setup()

    expect(screen.getByRole('heading', { name: 'Datenschutzerklärung' })).toBeInTheDocument()
    expect(await screen.findByText(/Verantwortlich ist Jane Doe/)).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Hinweis zum Training' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Zurück/ })).toHaveAttribute('href', '/')
  })

  it('still shows the policy when the contact cannot be read', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    mockApiFetch.mockRejectedValue(new Error('offline'))
    setup()

    expect(screen.getByRole('heading', { name: 'Privacy policy' })).toBeInTheDocument()
    await vi.waitFor(() => expect(warn).toHaveBeenCalled())
    expect(screen.getAllByText('(not set yet)').length).toBeGreaterThan(0)
    warn.mockRestore()
  })

  it('describes the coach review only while the opt-in is on (ai-trainer-ops#136)', async () => {
    setup()
    await screen.findByText(/The controller is Jane Doe/)
    expect(screen.queryByRole('heading', { name: /Coach review/ })).not.toBeInTheDocument()
  })

  it('names purpose, recipient, legal basis and withdrawal of the coach review', async () => {
    mockApiFetch.mockResolvedValue({ operatorName: 'Jane Doe', contactEmail: null, coachReviewOptIn: true })
    useAppStore.setState({ uiLanguage: 'de' })
    setup()

    const heading = await screen.findByRole('heading', { name: 'Coach-Review (nur mit deiner Einwilligung)' })
    const section = heading.closest('section')!
    expect(section).toHaveAttribute('id', 'coach-review')
    for (const fact of [/Anthropic als Auftragsverarbeiter, USA/, /Art\. 9 Abs\. 2 lit\. a/, /mindestens drei Sportler/, /ab dem nächsten Lauf/]) {
      expect(section).toHaveTextContent(fact)
    }
  })

  it('has the coach review in English too', async () => {
    mockApiFetch.mockResolvedValue({ operatorName: 'Jane Doe', contactEmail: null, coachReviewOptIn: true })
    setup()

    const section = (await screen.findByRole('heading', { name: 'Coach review (only with your consent)' })).closest('section')!
    expect(section).toHaveTextContent(/Anthropic as processor, USA/)
    expect(section).toHaveTextContent(/Art\. 9\(2\)\(a\)/)
  })

  it('names the body weight, as health data, in both languages (ai-trainer-ops#188)', async () => {
    setup()
    const data = (await screen.findByRole('heading', { name: 'Which data' })).closest('section')!
    expect(data).toHaveTextContent(/your body weight if you enter it/)
    const health = screen.getByRole('heading', { name: 'Health data' }).closest('section')!
    expect(health).toHaveTextContent(/Heart rate, body weight, effort/)
  })

  it('names every file an upload reads, FIT, GPX and TCX (ai-trainer-ops#311)', async () => {
    setup()
    const data = (await screen.findByRole('heading', { name: 'Which data' })).closest('section')!
    expect(data).toHaveTextContent(/from uploaded FIT, GPX or TCX files, or entered by you/)
  })

  it('names them in German too', async () => {
    useAppStore.setState({ uiLanguage: 'de' })
    setup()
    const data = (await screen.findByRole('heading', { name: 'Welche Daten' })).closest('section')!
    expect(data).toHaveTextContent(/aus hochgeladenen FIT-, GPX- oder TCX-Dateien oder von dir eingetragen/)
  })

  it('names the body weight in German too', async () => {
    useAppStore.setState({ uiLanguage: 'de' })
    setup()
    const data = (await screen.findByRole('heading', { name: 'Welche Daten' })).closest('section')!
    expect(data).toHaveTextContent(/dein Körpergewicht, wenn du es angibst/)
    const health = screen.getByRole('heading', { name: 'Gesundheitsdaten' }).closest('section')!
    expect(health).toHaveTextContent(/Herzfrequenz, Körpergewicht, Anstrengung/)
  })
})
