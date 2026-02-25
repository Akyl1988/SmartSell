import axios, { AxiosError } from 'axios'

const baseURL = import.meta.env.VITE_API_URL || 'http://localhost:8000'

export const apiClient = axios.create({
  baseURL,
  timeout: 20000,
  headers: { 'Content-Type': 'application/json' },
})

apiClient.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

apiClient.interceptors.response.use(
  (response) => response,
  (error: AxiosError) => {
    const status = error.response?.status
    if (status === 401) {
      window.dispatchEvent(new CustomEvent('auth:unauthorized'))
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
