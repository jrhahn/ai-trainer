import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import TotpSettings from './TotpSettings'
import type { TotpStatus } from '../services/totp'

const { mockFetchStatus, mockEnroll, mockConfirm, mockDisable, mockRevoke } = vi.hoisted(
  () => ({
    mockFetchStatus: vi.fn(),
    mockEnroll: vi.fn(),
    mockConfirm: vi.fn(),
    mockDisable: vi.fn(),
    mockRevoke: vi.fn(),
  }),
)

vi.mock('../services/totp', () => ({
  fetchTotpStatus: mockFetchStatus,
  startTotpEnrollment: mockEnroll,
  confirmTotp: mockConfirm,
  disableTotp: mockDisable,
  revokeTrustedDevices: mockRevoke,
}))

vi.mock('../store/useAppStore', () => ({
  useAppStore: (selector: (state: { authToken: string }) => unknown) =>
    selector({ authToken: 'test-token' }),
}))

function status(overrides: Partial<TotpStatus> = {}): TotpStatus {
  return {
    enabled: false,
    confirmedAt: null,
    recoveryCodesRemaining: 0,
    trustedDeviceCount: 0,
    ...overrides,
  }
}

function renderComponent() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={qc}>
      <TotpSettings />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  mockFetchStatus.mockResolvedValue(status())
})

describe('TotpSettings — enrollment', () => {
  it('offers setup when the factor is off', async () => {
    renderComponent()

    expect(
      await screen.findByRole('button', { name: /set up two-factor/i }),
    ).toBeInTheDocument()
  })

  it('renders the QR the backend produced rather than building one', async () => {
    /*
     * The SVG is server-rendered so no QR library enters the bundle and the CSP
     * needs no exception (#677). If this ever starts being generated client-side
     * that decision has been reversed by accident.
     */
    mockEnroll.mockResolvedValue({
      secret: 'AAAA BBBB CCCC',
      provisioningUri: 'otpauth://totp/x',
      qrSvg: '<svg data-testid="qr"><path d="M0 0"/></svg>',
    })
    const { container } = renderComponent()

    await userEvent.click(await screen.findByRole('button', { name: /set up two-factor/i }))

    await waitFor(() => {
      expect(container.querySelector('svg[data-testid="qr"]')).not.toBeNull()
    })
  })

  it('offers the secret as text for anyone who cannot scan', async () => {
    mockEnroll.mockResolvedValue({
      secret: 'AAAA BBBB CCCC',
      provisioningUri: 'otpauth://totp/x',
      qrSvg: '<svg/>',
    })
    renderComponent()

    await userEvent.click(await screen.findByRole('button', { name: /set up two-factor/i }))

    expect(await screen.findByText('AAAA BBBB CCCC')).toBeInTheDocument()
  })

  it('shows the recovery codes after confirming, and only then', async () => {
    /*
     * The codes come back once from confirmation and are never retrievable
     * again, so this panel is the single moment the user can save them. It
     * must not appear before, and must not vanish on its own.
     */
    mockEnroll.mockResolvedValue({
      secret: 'AAAA',
      provisioningUri: 'otpauth://totp/x',
      qrSvg: '<svg/>',
    })
    mockConfirm.mockResolvedValue(['aaaaa-bbbbb', 'ccccc-ddddd'])
    renderComponent()

    await userEvent.click(await screen.findByRole('button', { name: /set up two-factor/i }))
    await waitFor(() => screen.getByPlaceholderText('000000'))
    expect(screen.queryByText('aaaaa-bbbbb')).not.toBeInTheDocument()

    await userEvent.type(screen.getByPlaceholderText('000000'), '123456')
    await userEvent.click(screen.getByRole('button', { name: /turn on/i }))

    await waitFor(() => {
      expect(screen.getByText('aaaaa-bbbbb')).toBeInTheDocument()
      expect(screen.getByText('ccccc-ddddd')).toBeInTheDocument()
    })
    expect(screen.getByText(/shown once/i)).toBeInTheDocument()
  })

  it('keeps the codes on screen until they are explicitly dismissed', async () => {
    mockEnroll.mockResolvedValue({ secret: 'A', provisioningUri: 'u', qrSvg: '<svg/>' })
    mockConfirm.mockResolvedValue(['aaaaa-bbbbb'])
    // Left at enabled:false — that is the real state while enrolling, and
    // pretending otherwise removes the button this test has to click.
    renderComponent()

    await userEvent.click(await screen.findByRole('button', { name: /set up two-factor/i }))
    await waitFor(() => screen.getByPlaceholderText('000000'))
    await userEvent.type(screen.getByPlaceholderText('000000'), '123456')
    await userEvent.click(screen.getByRole('button', { name: /turn on/i }))
    await waitFor(() => screen.getByText('aaaaa-bbbbb'))

    await userEvent.click(screen.getByRole('button', { name: /i have saved them/i }))

    await waitFor(() => {
      expect(screen.queryByText('aaaaa-bbbbb')).not.toBeInTheDocument()
    })
  })

  it('surfaces a rejected confirmation code without enabling anything', async () => {
    mockEnroll.mockResolvedValue({ secret: 'A', provisioningUri: 'u', qrSvg: '<svg/>' })
    mockConfirm.mockRejectedValue(new Error('That code is not valid.'))
    renderComponent()

    await userEvent.click(await screen.findByRole('button', { name: /set up two-factor/i }))
    await waitFor(() => screen.getByPlaceholderText('000000'))
    await userEvent.type(screen.getByPlaceholderText('000000'), '000000')
    await userEvent.click(screen.getByRole('button', { name: /turn on/i }))

    expect(await screen.findByText(/that code is not valid/i)).toBeInTheDocument()
  })
})

describe('TotpSettings — when it is on', () => {
  it('reports the state and how many recovery codes are left', async () => {
    mockFetchStatus.mockResolvedValue(
      status({ enabled: true, recoveryCodesRemaining: 7, trustedDeviceCount: 2 }),
    )
    renderComponent()

    expect(await screen.findByText('On')).toBeInTheDocument()
    expect(screen.getByText(/7 recovery codes left/i)).toBeInTheDocument()
    expect(screen.getByText(/2 trusted devices/i)).toBeInTheDocument()
  })

  it('warns when the recovery codes are used up', async () => {
    /*
     * Zero codes left means a lost phone needs server access to fix, and the
     * user cannot tell from anywhere else that they are in that position.
     */
    mockFetchStatus.mockResolvedValue(status({ enabled: true, recoveryCodesRemaining: 0 }))
    renderComponent()

    expect(await screen.findByText(/no recovery codes left/i)).toBeInTheDocument()
  })

  it('offers to revoke devices only when there are some', async () => {
    mockFetchStatus.mockResolvedValue(
      status({ enabled: true, recoveryCodesRemaining: 5, trustedDeviceCount: 0 }),
    )
    renderComponent()

    await screen.findByText('On')
    expect(
      screen.queryByRole('button', { name: /revoke trusted devices/i }),
    ).not.toBeInTheDocument()
  })

  it('revokes trusted devices when asked', async () => {
    mockFetchStatus.mockResolvedValue(
      status({ enabled: true, recoveryCodesRemaining: 5, trustedDeviceCount: 3 }),
    )
    mockRevoke.mockResolvedValue(undefined)
    renderComponent()

    await userEvent.click(
      await screen.findByRole('button', { name: /revoke trusted devices/i }),
    )

    await waitFor(() => expect(mockRevoke).toHaveBeenCalledWith('test-token'))
  })

  it('requires the password to turn it off', async () => {
    /* A borrowed unlocked browser must not be able to strip the factor. */
    mockFetchStatus.mockResolvedValue(status({ enabled: true, recoveryCodesRemaining: 5 }))
    mockDisable.mockResolvedValue(undefined)
    renderComponent()

    await userEvent.click(await screen.findByRole('button', { name: /turn off/i }))
    const field = screen.getByLabelText(/confirm your password/i)
    await userEvent.type(field, 'my-password')
    await userEvent.click(screen.getByRole('button', { name: /confirm/i }))

    await waitFor(() =>
      expect(mockDisable).toHaveBeenCalledWith('test-token', 'my-password'),
    )
  })

  it('cannot be turned off with an empty password field', async () => {
    mockFetchStatus.mockResolvedValue(status({ enabled: true, recoveryCodesRemaining: 5 }))
    renderComponent()

    await userEvent.click(await screen.findByRole('button', { name: /turn off/i }))

    expect(screen.getByRole('button', { name: /confirm/i })).toBeDisabled()
    expect(mockDisable).not.toHaveBeenCalled()
  })

  it('reports a wrong password instead of appearing to succeed', async () => {
    mockFetchStatus.mockResolvedValue(status({ enabled: true, recoveryCodesRemaining: 5 }))
    mockDisable.mockRejectedValue(new Error('Incorrect password.'))
    renderComponent()

    await userEvent.click(await screen.findByRole('button', { name: /turn off/i }))
    await userEvent.type(screen.getByLabelText(/confirm your password/i), 'wrong')
    await userEvent.click(screen.getByRole('button', { name: /confirm/i }))

    expect(await screen.findByText(/incorrect password/i)).toBeInTheDocument()
  })
})
