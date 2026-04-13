import { apiFetch } from './api'

interface AuthResponse {
  access_token: string
  token_type: string
}

export async function register(name: string, email: string, password: string): Promise<string | null> {
  const response = await apiFetch<{ access_token: string; token_type: string } | undefined>('/auth/register', {
    method: 'POST',
    body: { name, email, password },
  })
  return response?.access_token ?? null
}

export async function login(email: string, password: string): Promise<string> {
  const response = await apiFetch<AuthResponse>('/auth/login', {
    method: 'POST',
    body: { email, password },
  })
  return response.access_token
}

export async function getSessionToken(): Promise<string> {
  const response = await apiFetch<AuthResponse>('/auth/session')
  return response.access_token
}
