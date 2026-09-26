import { beforeEach, describe, expect, it, vi } from 'vitest'

const mockApiFetch = vi.hoisted(() => vi.fn())
vi.mock('./api', () => ({ apiFetch: mockApiFetch }))

import { loginStep, loginWithTotp } from './auth'
import {
  confirmTotp,
  disableTotp,
  revokeTrustedDevices,
  startTotpEnrollment,
} from './totp'

beforeEach(() => {
  vi.clearAllMocks()
})

describe('loginStep', () => {
  it('returns a token when no second factor is configured', async () => {
    mockApiFetch.mockImplementation(async (path: string) => {
      if (path === '/auth/captcha/challenge') throw new Error('Not Found')
      return { access_token: 'jwt-abc', token_type: 'bearer' }
    })

    await expect(loginStep('a@example.com', 'pw')).resolves.toEqual({
      kind: 'token',
      token: 'jwt-abc',
    })
  })

  it('returns a challenge instead of a token when a code is needed', async () => {
    /*
     * The server answers 200 for both, because the password *was* accepted.
     * Treating this as a failure would leave the caller unable to tell it from
     * a wrong password — which is the whole reason it is not a 401.
     */
    mockApiFetch.mockResolvedValue({ mfaRequired: true, challenge: 'chal-1' })

    await expect(loginStep('a@example.com', 'pw')).resolves.toEqual({
      kind: 'mfa',
      challenge: 'chal-1',
    })
  })
})

describe('loginWithTotp', () => {
  it('sends the challenge, the code and the remember flag', async () => {
    mockApiFetch.mockResolvedValue({ access_token: 'jwt-xyz', token_type: 'bearer' })

    const token = await loginWithTotp('chal-1', '123456', true)

    expect(token).toBe('jwt-xyz')
    expect(mockApiFetch).toHaveBeenCalledWith('/auth/login/totp', {
      method: 'POST',
      body: { challenge: 'chal-1', code: '123456', rememberDevice: true },
    })
  })

  it('sends rememberDevice in camelCase, which the backend maps by alias', async () => {
    /*
     * Guarding a bug that cost real time: the request model was a plain
     * BaseModel, so `rememberDevice` did not map to `remember_device` and the
     * flag was silently dropped — the device was never trusted and nothing
     * reported why.
     */
    mockApiFetch.mockResolvedValue({ access_token: 'x', token_type: 'bearer' })

    await loginWithTotp('c', '1', false)

    const body = mockApiFetch.mock.calls[0][1].body
    expect(Object.keys(body)).toContain('rememberDevice')
    expect(Object.keys(body)).not.toContain('remember_device')
  })
})

describe('totp settings calls', () => {
  it('returns the recovery codes from confirmation', async () => {
    mockApiFetch.mockResolvedValue({ recoveryCodes: ['aaaaa-bbbbb', 'ccccc-ddddd'] })

    await expect(confirmTotp('token', '123456')).resolves.toEqual([
      'aaaaa-bbbbb',
      'ccccc-ddddd',
    ])
  })

  it('sends the password when disabling, not just the session token', async () => {
    mockApiFetch.mockResolvedValue(undefined)

    await disableTotp('token', 'my-password')

    expect(mockApiFetch).toHaveBeenCalledWith('/auth/totp/disable', {
      method: 'POST',
      token: 'token',
      body: { password: 'my-password' },
    })
  })

  it('revokes trusted devices', async () => {
    /* The lost-laptop path — the whole reason a 30-day trust is acceptable. */
    mockApiFetch.mockResolvedValue(undefined)

    await revokeTrustedDevices('token')

    expect(mockApiFetch).toHaveBeenCalledWith('/auth/totp/trusted-devices/revoke', {
      method: 'POST',
      token: 'token',
    })
  })

  it('asks the backend for the QR rather than building one', async () => {
    mockApiFetch.mockResolvedValue({
      secret: 'AAAA BBBB',
      provisioningUri: 'otpauth://totp/x',
      qrSvg: '<svg/>',
    })

    const enrollment = await startTotpEnrollment('token')

    expect(enrollment.qrSvg).toBe('<svg/>')
  })
})
