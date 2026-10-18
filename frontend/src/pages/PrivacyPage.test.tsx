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
})
