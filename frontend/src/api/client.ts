import axios, { AxiosError, InternalAxiosRequestConfig } from 'axios'
import { bootstrapTokenStore, clearSessionTokens, getAccessToken, getRefreshToken, setSessionTokens } from '../auth/tokenStore'

const baseURL = import.meta.env.VITE_API_URL || 'http://localhost:8000'

export const apiClient = axios.create({
  baseURL,
  timeout: 20000,
  headers: { 'Content-Type': 'application/json' },
})

const refreshClient = axios.create({
  baseURL,
  timeout: 20000,
  headers: { 'Content-Type': 'application/json' },
})

type RetryConfig = InternalAxiosRequestConfig & {
  _smartsellRetry?: boolean
  skipAuthRefresh?: boolean
}

let refreshInFlight: Promise<string | null> | null = null

bootstrapTokenStore()

function extractErrorSignal(error: AxiosError): { status?: number; detail?: string; code?: string } {
  const status = error.response?.status
  const data = error.response?.data
  if (data && typeof data === 'object') {
    const obj = data as Record<string, unknown>
    const detail = typeof obj.detail === 'string' ? obj.detail : undefined
    const code = typeof obj.code === 'string' ? obj.code : undefined
    return { status, detail, code }
  }
  return { status }
}

function shouldAttemptRefresh(error: AxiosError, config: RetryConfig | undefined): boolean {
  if (!config || config._smartsellRetry || config.skipAuthRefresh) {
    return false
  }

  const requestUrl = (config.url || '').toLowerCase()
  if (requestUrl.includes('/api/v1/auth/login') || requestUrl.includes('/api/v1/auth/refresh') || requestUrl.includes('/api/v1/auth/logout')) {
    return false
  }

  const { status, detail, code } = extractErrorSignal(error)
  if (status !== 401) {
    return false
  }

  const signal = (detail || code || '').toLowerCase()
  if (signal === 'invalid_credentials') {
    return false
  }

  return Boolean(getRefreshToken())
}

async function refreshAccessTokenSingleFlight(): Promise<string | null> {
  if (!refreshInFlight) {
    refreshInFlight = (async () => {
      const refreshToken = getRefreshToken()
      if (!refreshToken) {
        return null
      }

      try {
        const response = await refreshClient.post('/api/v1/auth/refresh', { refresh_token: refreshToken })
        const tokens = response.data as { access_token?: string; refresh_token?: string }
        if (!tokens.access_token || !tokens.refresh_token) {
          return null
        }
        setSessionTokens(tokens.access_token, tokens.refresh_token)
        return tokens.access_token
      } catch {
        return null
      }
    })().finally(() => {
      refreshInFlight = null
    })
  }

  return refreshInFlight
}

apiClient.interceptors.request.use((config) => {
  return (async () => {
    if (!config.skipAuthRefresh && refreshInFlight) {
      await refreshInFlight.catch(() => null)
    }

    const token = getAccessToken()
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  })()
})

function dispatchUnauthorized(reason: string): void {
  window.dispatchEvent(new CustomEvent('auth:unauthorized', { detail: { reason } }))
}

apiClient.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const config = error.config as RetryConfig | undefined

    if (shouldAttemptRefresh(error, config)) {
      const refreshedAccessToken = await refreshAccessTokenSingleFlight()
      if (refreshedAccessToken && config) {
        config._smartsellRetry = true
        config.headers = config.headers || {}
        config.headers.Authorization = `Bearer ${refreshedAccessToken}`
        return apiClient.request(config)
      }

      clearSessionTokens()
      dispatchUnauthorized('session_expired')
      return Promise.reject(error)
    }

    const status = error.response?.status
    if (status === 401) {
      clearSessionTokens()
      dispatchUnauthorized('unauthorized')
    }
    if (status === 402) {
      window.dispatchEvent(new CustomEvent('auth:payment_required'))
    }
    return Promise.reject(error)
  }
)

export function getHttpErrorInfo(error: unknown): { status?: number; message: string } {
  if (axios.isAxiosError(error)) {
    const status = error.response?.status
    const data = error.response?.data
    if (typeof data === 'string' && data.trim().length > 0) {
      return { status, message: data }
    }

    if (data && typeof data === 'object') {
      const maybeDetail = (data as { detail?: unknown }).detail
      if (typeof maybeDetail === 'string' && maybeDetail.trim().length > 0) {
        return { status, message: maybeDetail }
      }

      const maybeMessage = (data as { message?: unknown }).message
      if (typeof maybeMessage === 'string' && maybeMessage.trim().length > 0) {
        return { status, message: maybeMessage }
      }
    }

    if (error.message) {
      return { status, message: error.message }
    }

    return { status, message: 'Unknown error' }
  }

  if (error instanceof Error && error.message) {
    return { message: error.message }
  }

  return { message: 'Unknown error' }
}
