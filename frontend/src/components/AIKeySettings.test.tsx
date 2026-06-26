import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import AIKeySettings from './AIKeySettings'
import type { AIKeyStatus } from '../services/user'

const { mockFetchStatus, mockSaveKey, mockDeleteKey, mockTestKey } = vi.hoisted(() => ({
  mockFetchStatus: vi.fn(),
  mockSaveKey: vi.fn(),
  mockDeleteKey: vi.fn(),
  mockTestKey: vi.fn(),
}))

vi.mock('../services/user', () => ({
  fetchAIKeyStatus: mockFetchStatus,
  saveAIKey: mockSaveKey,
  deleteAIKey: mockDeleteKey,
  testAIKey: mockTestKey,
}))

vi.mock('../store/useAppStore', () => ({
  useAppStore: (selector: (state: { authToken: string }) => unknown) =>
    selector({ authToken: 'test-token' }),
}))

function status(overrides: Partial<AIKeyStatus> = {}): AIKeyStatus {
  return { hasOpenaiKey: false, hasGeminiKey: false, ...overrides } as AIKeyStatus
}

function renderComponent() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={qc}>
      <AIKeySettings />
    </QueryClientProvider>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  mockFetchStatus.mockResolvedValue(status())
})

describe('AIKeySettings', () => {
  it('shows "No key saved" when no provider key is stored', async () => {
    renderComponent()
    expect(await screen.findByText('No key saved')).toBeInTheDocument()
  })

  it('shows the saved badge and a remove button when a key exists', async () => {
    mockFetchStatus.mockResolvedValue(status({ hasOpenaiKey: true }))
    renderComponent()
    expect(await screen.findByText('Key saved')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Remove key/ })).toBeInTheDocument()
  })

  it('disables Test and Save while the input is empty', async () => {
    renderComponent()
    await screen.findByText('No key saved')
    expect(screen.getByRole('button', { name: /Test key/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: /Save key/ })).toBeDisabled()
  })

  it('validates a key and shows a success message', async () => {
    mockTestKey.mockResolvedValue(undefined)
    renderComponent()
    await screen.findByText('No key saved')

    await userEvent.type(screen.getByPlaceholderText('sk-...'), 'sk-secret')
    await userEvent.click(screen.getByRole('button', { name: /Test key/ }))

    expect(await screen.findByText('Key is valid and working.')).toBeInTheDocument()
    expect(mockTestKey).toHaveBeenCalledWith('test-token', 'openai', 'sk-secret')
  })

  it('surfaces a validation error from the backend', async () => {
    mockTestKey.mockRejectedValue(new Error('Invalid key'))
    renderComponent()
    await screen.findByText('No key saved')

    await userEvent.type(screen.getByPlaceholderText('sk-...'), 'sk-bad')
    await userEvent.click(screen.getByRole('button', { name: /Test key/ }))

    expect(await screen.findByText('Invalid key')).toBeInTheDocument()
  })

  it('saves the key and confirms success', async () => {
    mockSaveKey.mockResolvedValue(undefined)
    renderComponent()
    await screen.findByText('No key saved')

    await userEvent.type(screen.getByPlaceholderText('sk-...'), 'sk-secret')
    await userEvent.click(screen.getByRole('button', { name: /Save key/ }))

    await waitFor(() =>
      expect(mockSaveKey).toHaveBeenCalledWith('test-token', 'openai', 'sk-secret')
    )
    expect(await screen.findByText('Key saved successfully.')).toBeInTheDocument()
  })

  it('switches provider and shows the Gemini placeholder', async () => {
    renderComponent()
    await screen.findByText('No key saved')

    await userEvent.click(screen.getByRole('button', { name: /Google Gemini/ }))
    expect(screen.getByPlaceholderText('AIza...')).toBeInTheDocument()
  })

  it('removes a saved key', async () => {
    mockFetchStatus.mockResolvedValue(status({ hasOpenaiKey: true }))
    mockDeleteKey.mockResolvedValue(undefined)
    renderComponent()

    await userEvent.click(await screen.findByRole('button', { name: /Remove key/ }))

    await waitFor(() => expect(mockDeleteKey).toHaveBeenCalledWith('test-token', 'openai'))
  })
})
