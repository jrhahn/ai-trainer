/**
 * Managing the second factor from the settings page (#688).
 *
 * Everything here needs a live session; the two login-time calls live in
 * `auth.ts` because they run before there is one.
 */
import { apiFetch } from './api'

export interface TotpStatus {
  enabled: boolean
  confirmedAt: string | null
  recoveryCodesRemaining: number
  trustedDeviceCount: number
}

export interface TotpEnrollment {
  /** Grouped for anyone typing it in rather than scanning. */
  secret: string
  provisioningUri: string
  /** Rendered server-side, so no QR library and no CSP exception (#677). */
  qrSvg: string
}

export function fetchTotpStatus(token: string): Promise<TotpStatus> {
  return apiFetch<TotpStatus>('/auth/totp/status', { token })
}

export function startTotpEnrollment(token: string): Promise<TotpEnrollment> {
  return apiFetch<TotpEnrollment>('/auth/totp/enroll', { method: 'POST', token })
}

/** Returns the recovery codes — shown once and never retrievable again. */
export async function confirmTotp(token: string, code: string): Promise<string[]> {
  const response = await apiFetch<{ recoveryCodes: string[] }>('/auth/totp/confirm', {
    method: 'POST',
    token,
    body: { code },
  })
  return response.recoveryCodes
}

/** Needs the password: a borrowed unlocked browser must not be enough. */
export function disableTotp(token: string, password: string): Promise<void> {
  return apiFetch<void>('/auth/totp/disable', {
    method: 'POST',
    token,
    body: { password },
  })
}

export function revokeTrustedDevices(token: string): Promise<void> {
  return apiFetch<void>('/auth/totp/trusted-devices/revoke', { method: 'POST', token })
}
