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

const mockExpert = vi.hoisted(() => ({ value: false }))
vi.mock('../store/useAppStore', () => ({
  useAppStore: (selector: (state: { authToken: string; isExpertMode: boolean }) => unknown) =>
    selector({ authToken: 'test-token', isExpertMode: mockExpert.value }),
}))

function status(overrides: Partial<AIKeyStatus> = {}): AIKeyStatus {
  return { provider: 'openai', hasOpenaiKey: false, hasGeminiKey: false, ...overrides } as AIKeyStatus
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
  mockExpert.value = false
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

  it('shows the free allowance while the owner pays (ai-trainer-ops#79)', async () => {
    mockFetchStatus.mockResolvedValue(
      status({ allowanceTokens: 5_000_000, allowanceUsed: 1_250_000, allowanceWindowDays: 30 }),
    )
    renderComponent()
    expect(await screen.findByTestId('free-allowance')).toHaveTextContent(
      /1,250k of 5,000k tokens used in the last 30 days/,
    )
  })

  it('shows no allowance with an own key', async () => {
    mockFetchStatus.mockResolvedValue(status({ hasGeminiKey: true, hasOpenaiKey: true, allowanceTokens: null }))
    renderComponent()
    await screen.findByText('Key saved')
    expect(screen.queryByTestId('free-allowance')).not.toBeInTheDocument()
  })

  it('offers nothing to press while the input is empty', async () => {
    renderComponent()
    await screen.findByText('No key saved')
    expect(screen.getByRole('button', { name: /Test and save key/ })).toBeDisabled()
  })

  it('tests the key, then saves it, in one press (ai-trainer-ops#93)', async () => {
    mockTestKey.mockResolvedValue(undefined)
    mockSaveKey.mockResolvedValue(undefined)
    renderComponent()
    await userEvent.type(await screen.findByPlaceholderText('sk-...'), 'sk-secret')
    await userEvent.click(screen.getByRole('button', { name: /Test and save key/ }))

    await waitFor(() =>
      expect(mockSaveKey).toHaveBeenCalledWith('test-token', 'openai', 'sk-secret')
    )
    expect(mockTestKey).toHaveBeenCalledWith('test-token', 'openai', 'sk-secret')
    expect(await screen.findByText('The key works and is saved.')).toBeInTheDocument()
  })

  it('never stores a key the provider refuses', async () => {
    mockTestKey.mockRejectedValue(new Error('Invalid key'))
    renderComponent()
    await userEvent.type(await screen.findByPlaceholderText('sk-...'), 'sk-bad')
    await userEvent.click(screen.getByRole('button', { name: /Test and save key/ }))

    expect(await screen.findByText('Invalid key')).toBeInTheDocument()
    expect(mockSaveKey).not.toHaveBeenCalled()
  })

  it('switches provider and shows the Gemini placeholder', async () => {
    renderComponent()

    await userEvent.click(await screen.findByRole('button', { name: /Google Gemini/ }))
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

describe('the provider block and the shared key (ai-trainer-ops#93)', () => {
  it('opens on the provider the account runs on, Gemini by default', async () => {
    mockFetchStatus.mockResolvedValue(status({ provider: 'gemini' }))
    renderComponent()

    expect(await screen.findByPlaceholderText('AIza...')).toBeInTheDocument()
  })

  it('folds the key on the shared key, Gemini only, outside expert mode (ai-trainer-ops#217)', async () => {
    mockFetchStatus.mockResolvedValue(status({ provider: 'gemini', keyRequired: false }))
    const { container } = renderComponent()

    expect(await screen.findByText('Own key (optional)')).toBeInTheDocument()
    expect(container.querySelector('details')).not.toHaveAttribute('open')
    expect(screen.queryByRole('button', { name: 'OpenAI' })).not.toBeInTheDocument()
    expect(screen.getByText('Google Gemini API Key')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'How to create a Google Gemini key' }).closest('details')).not.toBeNull()
  })

  it('stays folded while the status loads', () => {
    mockFetchStatus.mockReturnValue(new Promise(() => {}))
    renderComponent()

    expect(screen.getByText('Own key (optional)')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'OpenAI' })).not.toBeInTheDocument()
  })

  it.each([
    ['in expert mode', true, { keyRequired: false }],
    ['with a key of their own', false, { keyRequired: false, hasGeminiKey: true }],
    ['when the server needs a key', false, { keyRequired: true }],
  ])('shows the providers and no fold %s', async (_case, expert, overrides) => {
    mockExpert.value = expert
    mockFetchStatus.mockResolvedValue(status(overrides))
    const { container } = renderComponent()

    expect(await screen.findByRole('button', { name: 'OpenAI' })).toBeInTheDocument()
    expect(container.querySelector('details')).toBeNull()
  })

  it('says no key is needed while the app pays', async () => {
    mockFetchStatus.mockResolvedValue(status({ keyRequired: false }))
    renderComponent()

    expect(await screen.findByTestId('shared-key')).toHaveTextContent(/you need no key of your own/)
  })

  it.each([
    ['the server needs one', { keyRequired: true }],
    ['the athlete has one', { keyRequired: false, hasGeminiKey: true }],
  ])('asks for a key when %s', async (_why, overrides) => {
    mockFetchStatus.mockResolvedValue(status(overrides))
    renderComponent()

    expect(await screen.findByText(/Supply your own API key/)).toBeInTheDocument()
    expect(screen.queryByTestId('shared-key')).not.toBeInTheDocument()
  })
})

describe('anchor target', () => {
  it('carries the id the AI Provider section links to', () => {
    /*
     * The pointer added in #691 is only useful if it lands somewhere. If this
     * id is renamed, the link in SettingsPage silently becomes a no-op and the
     * section is hard to find again — which was the original complaint.
     */
    const { container } = renderComponent()

    expect(container.querySelector('#your-ai-provider-key')).not.toBeNull()
  })
})

describe('the messages the key card can show (ai-trainer-ops#104)', () => {
  it('falls back to its own words when a save or a removal fails without a message', async () => {
    mockTestKey.mockResolvedValue(undefined)
    mockSaveKey.mockRejectedValue(new Error(''))
    renderComponent()
    await userEvent.type(await screen.findByPlaceholderText('sk-...'), 'sk-secret')
    await userEvent.click(screen.getByRole('button', { name: /Test and save key/ }))
    expect(await screen.findByText('Failed to save key.')).toBeInTheDocument()
  })

  it('names a failed removal that gives no message', async () => {
    mockFetchStatus.mockResolvedValue(status({ hasOpenaiKey: true }))
    mockDeleteKey.mockRejectedValue(new Error(''))
    renderComponent()
    await userEvent.click(await screen.findByRole('button', { name: /Remove key/ }))
    expect(await screen.findByText('Failed to remove key.')).toBeInTheDocument()
  })

  it('names a refused key when the check gives no message', async () => {
    mockTestKey.mockRejectedValue('nope')
    renderComponent()
    await userEvent.type(await screen.findByPlaceholderText('sk-...'), 'sk-bad')
    await userEvent.click(screen.getByRole('button', { name: /Test and save key/ }))
    expect(await screen.findByText('Key validation failed.')).toBeInTheDocument()
  })

  it('counts an allowance nothing has used yet, over the default window', async () => {
    mockFetchStatus.mockResolvedValue(status({ allowanceTokens: 5_000_000 }))
    renderComponent()
    expect(await screen.findByTestId('free-allowance')).toHaveTextContent(
      /0k of 5,000k tokens used in the last 30 days/,
    )
  })

  it('says it is removing while the key is removed', async () => {
    mockFetchStatus.mockResolvedValue(status({ hasOpenaiKey: true }))
    mockDeleteKey.mockReturnValue(new Promise(() => {}))
    renderComponent()
    await userEvent.click(await screen.findByRole('button', { name: /Remove key/ }))
    expect(await screen.findByRole('button', { name: /Removing…/ })).toBeInTheDocument()
  })

  it('says it is testing while the provider checks the key', async () => {
    mockTestKey.mockReturnValue(new Promise(() => {}))
    renderComponent()
    await userEvent.type(await screen.findByPlaceholderText('sk-...'), 'sk-secret')
    await userEvent.click(screen.getByRole('button', { name: /Test and save key/ }))
    expect(await screen.findByRole('button', { name: /Testing…/ })).toBeInTheDocument()
  })
})
