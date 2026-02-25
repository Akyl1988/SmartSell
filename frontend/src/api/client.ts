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
