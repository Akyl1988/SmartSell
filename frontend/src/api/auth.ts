import { apiClient } from './client'

export type TokenResponse = {
  access_token: string
  refresh_token: string
  token_type?: 'bearer'
  expires_in: number
}

export type LoginPayload = {
  identifier: string
  password?: string | null
  otp_code?: string | null
}

export type RefreshTokenRequest = {
  refresh_token: string
}

// OpenAPI defines /auth/me as an untyped object; this is a minimal assumed shape.
export type MeResponse = {
  id?: number | string
  phone?: string
  email?: string | null
  full_name?: string | null
  company_name?: string | null
  plan?: string | null
  role?: string | null
  is_superuser?: boolean | null
}

export async function login(payload: LoginPayload): Promise<TokenResponse> {
  const { data } = await apiClient.post<TokenResponse>('/api/v1/auth/login', payload)
  return data
}

export async function refresh(payload?: RefreshTokenRequest | null): Promise<TokenResponse> {
  const { data } = await apiClient.post<TokenResponse>('/api/v1/auth/refresh', payload ?? null)
  return data
}

export async function me(): Promise<MeResponse> {
  const { data } = await apiClient.get<MeResponse>('/api/v1/auth/me')
  return data
}

export async function logout(payload?: RefreshTokenRequest | null) {
  const { data } = await apiClient.post('/api/v1/auth/logout', payload ?? null)
  return data
}
