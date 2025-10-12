// API configuration and client for SmartSell
// Works with direct backend URL from VITE_API_URL.
// When VITE_USE_MOCKS=1, forces same-origin base ('') so MSW matches easily.

import axios from 'axios'

/** Ensure no trailing slash on base URL */
function normalizeBase(url) {
  if (!url) return ''
  return url.endsWith('/') ? url.slice(0, -1) : url
}

/** Small helper to build query strings safely */
function qs(params = {}) {
  const entries = Object.entries(params).filter(
    ([, v]) => v !== undefined && v !== null
  )
  if (entries.length === 0) return ''
  const search = new URLSearchParams()
  for (const [k, v] of entries) search.set(k, String(v))
  return `?${search.toString()}`
}

class ApiClient {
  constructor() {
    const useMocks =
      typeof import.meta !== 'undefined' &&
      import.meta.env &&
      import.meta.env.VITE_USE_MOCKS === '1'

    const baseFromEnv =
      (typeof import.meta !== 'undefined' &&
        import.meta.env &&
        import.meta.env.VITE_API_URL) ||
      'http://localhost:8000'

    // В режиме моков — всегда относительные пути (same-origin)
    this.baseURL = useMocks ? '' : normalizeBase(baseFromEnv)

    this.client = axios.create({
      baseURL: this.baseURL,
      timeout: 20000,
      headers: { 'Content-Type': 'application/json' },
    })

    // Request interceptor — здесь можно прикрутить токен
    this.client.interceptors.request.use((config) => {
      // const token = localStorage.getItem('token')
      // if (token) config.headers.Authorization = `Bearer ${token}`
      return config
    })

    // Response interceptor — унифицируем ошибки
    this.client.interceptors.response.use(
      (res) => res,
      (error) => {
        const msg =
          error?.response?.data?.detail ||
          error?.response?.data?.message ||
          error?.message ||
          'Request failed'
        return Promise.reject(new Error(msg))
      }
    )
  }

  // ---------- Health ----------
  async healthCheck() {
    const { data } = await this.client.get('/health')
    return data
  }

  // ---------- Campaigns ----------
  async getCampaigns(skip = 0, limit = 100) {
    const { data } = await this.client.get(
      `/api/v1/campaigns/${qs({ skip, limit })}`
    )
    return data // массив либо { items, total, ... }
  }

  async getCampaign(campaignId) {
    if (campaignId == null) throw new Error('campaignId is required')
    const { data } = await this.client.get(`/api/v1/campaigns/${campaignId}`)
    return data
  }

  async createCampaign(campaignData) {
    if (!campaignData || typeof campaignData !== 'object') {
      throw new Error('campaignData must be an object')
    }
    const { data } = await this.client.post('/api/v1/campaigns/', campaignData)
    return data
  }

  async updateCampaign(campaignId, campaignData) {
    if (campaignId == null) throw new Error('campaignId is required')
    const { data } = await this.client.put(
      `/api/v1/campaigns/${campaignId}`,
      campaignData || {}
    )
    return data
  }

  async deleteCampaign(campaignId) {
    if (campaignId == null) throw new Error('campaignId is required')
    const { data } = await this.client.delete(`/api/v1/campaigns/${campaignId}`)
    return data
  }

  // ---------- Optional: Campaign Messages ----------
  async listCampaignMessages(campaignId, skip = 0, limit = 100) {
    if (campaignId == null) throw new Error('campaignId is required')
    const { data } = await this.client.get(
      `/api/v1/campaigns/${campaignId}/messages${qs({ skip, limit })}`
    )
    return data
  }

  async createCampaignMessage(campaignId, payload) {
    if (campaignId == null) throw new Error('campaignId is required')
    const { data } = await this.client.post(
      `/api/v1/campaigns/${campaignId}/messages`,
      payload || {}
    )
    return data
  }
}

// Singleton instance
const apiClient = new ApiClient()
export default apiClient

// Named exports (handy for tests or direct imports)
export const {
  healthCheck,
  getCampaigns,
  getCampaign,
  createCampaign,
  updateCampaign,
  deleteCampaign,
  listCampaignMessages,
  createCampaignMessage,
} = {
  healthCheck: (...a) => apiClient.healthCheck(...a),
  getCampaigns: (...a) => apiClient.getCampaigns(...a),
  getCampaign: (...a) => apiClient.getCampaign(...a),
  createCampaign: (...a) => apiClient.createCampaign(...a),
  updateCampaign: (...a) => apiClient.updateCampaign(...a),
  deleteCampaign: (...a) => apiClient.deleteCampaign(...a),
  listCampaignMessages: (...a) => apiClient.listCampaignMessages(...a),
  createCampaignMessage: (...a) => apiClient.createCampaignMessage(...a),
}
