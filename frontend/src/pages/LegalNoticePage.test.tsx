import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import LegalNoticePage from './LegalNoticePage'
import { addressLines } from '../utils/addressLines'
import { useAppStore } from '../store/useAppStore'

const mockApiFetch = vi.hoisted(() => vi.fn())
vi.mock('../services/api', () => ({ apiFetch: mockApiFetch }))

function setup() {
  return render(
    <MemoryRouter>
      <LegalNoticePage />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  mockApiFetch.mockResolvedValue({
    operatorName: 'Jane Doe',
    operatorAddress: 'Main Street 1, 12345 Town',
    contactEmail: 'hello@example.com',
  })
})

afterEach(() => {
  useAppStore.setState({ uiLanguage: 'auto' })
})

describe('LegalNoticePage (ai-trainer-ops#1)', () => {
  it('gives name, postal address and email from the server', async () => {
    setup()

    expect(screen.getByRole('heading', { name: 'Legal notice' })).toBeInTheDocument()
    expect(await screen.findByText('Jane Doe')).toBeInTheDocument()
    // The address as the lines of a letter, not one run-on line.
    expect(screen.getByText('Main Street 1')).toBeInTheDocument()
    expect(screen.getByText('12345 Town')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'hello@example.com' })).toHaveAttribute('href', 'mailto:hello@example.com')
    expect(screen.getByRole('link', { name: 'Privacy policy' })).toHaveAttribute('href', '/privacy')
    expect(mockApiFetch).toHaveBeenCalledWith('/auth/legal')
  })

  it('says what is not set yet instead of guessing', async () => {
    mockApiFetch.mockResolvedValue({ operatorName: null, operatorAddress: null, contactEmail: null })
    setup()

    expect(await screen.findAllByText('(not set yet)')).toHaveLength(3)
    expect(screen.queryByRole('link', { name: /@/ })).not.toBeInTheDocument()
  })

  it('is German in a German interface', async () => {
    useAppStore.setState({ uiLanguage: 'de' })
    setup()

    expect(screen.getByRole('heading', { name: 'Impressum' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Angaben gemäß § 5 DDG' })).toBeInTheDocument()
    expect(await screen.findByText('Jane Doe')).toBeInTheDocument()
    expect(screen.getByText(/E-Mail:/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Datenschutz' })).toHaveAttribute('href', '/privacy')
    expect(screen.getByRole('link', { name: /Zurück/ })).toHaveAttribute('href', '/')
  })

  it('still shows the page when the server cannot be read', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    mockApiFetch.mockRejectedValue(new Error('offline'))
    setup()

    expect(screen.getByRole('heading', { name: 'Legal notice' })).toBeInTheDocument()
    await vi.waitFor(() => expect(warn).toHaveBeenCalled())
    expect(screen.getAllByText('(not set yet)')).toHaveLength(3)
    warn.mockRestore()
  })
})

describe('addressLines', () => {
  it('splits on commas and drops empty parts', () => {
    expect(addressLines(' Main Street 1 ,, 12345 Town ')).toEqual(['Main Street 1', '12345 Town'])
    expect(addressLines(null)).toEqual([])
    expect(addressLines('')).toEqual([])
  })
})
