// API configuration and client for SmartSell
// Works with direct backend URL from VITE_API_URL.
// When VITE_USE_MOCKS=1, forces same-origin base ('') so MSW matches easily.

import axios from 'axios'

/** Ensure no trailing slash on base URL */
function normalizeBase(url) {
  if (!url) return ''
  return url.endsWith('/') ? url.slice(0, -1) : url
}

/** Small helper to build query strings safely (arrays supported: k=a&k=b) */
function qs(params = {}) {
  const search = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null) continue
    if (Array.isArray(v)) {
      for (const item of v) {
        if (item === undefined || item === null) continue
        search.append(k, String(item))
      }
    } else {
      search.set(k, String(v))
    }
  }
  const s = search.toString()
  return s ? `?${s}` : ''
}

/** Exponential backoff retry wrapper (for idempotent GETs) */
async function withRetry(fn, { retries = 2, baseDelay = 250 } = {}) {
  let lastErr
  for (let i = 0; i <= retries; i++) {
    try {
      return await fn()
    } catch (e) {
      lastErr = e
      if (i === retries) break
      const delay = baseDelay * Math.pow(2, i)
      await new Promise((r) => setTimeout(r, delay))
    }
  }
  throw lastErr
}

/** Optional file download helper */
function downloadBlob(data, filename, type = 'application/octet-stream') {
  const blob = new Blob([data], { type })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
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

  // -------------------- Health --------------------
  async healthCheck() {
    // Пытаемся /health, при неудаче — /api/_health
    return withRetry(async () => {
      try {
        const { data } = await this.client.get('/health')
        return data
      } catch {
        const { data } = await this.client.get('/api/_health')
        return data
      }
    })
  }

  // -------------------- Campaigns --------------------
  async getCampaigns(skip = 0, limit = 100) {
    // FIX: без лишнего слэша перед query
    const { data } = await this.client.get(
      `/api/v1/campaigns${qs({ skip, limit })}`
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
    // допускаем trailing slash (совместимость), backend FastAPI обычно терпим
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

  // -------------------- Kaspi: Tokens & Connect --------------------
  async kaspiConnect({ store_name, token, verify = true, save = true }) {
    if (!store_name || !token) throw new Error('store_name and token are required')
    const { data } = await this.client.post('/api/v1/kaspi/connect', {
      store_name,
      token,
      verify,
      save,
    })
    return data // { ok, store_name, verified, saved, adapter_health, message }
  }

  async kaspiUpsertToken({ store_name, token }) {
    if (!store_name || !token) throw new Error('store_name and token are required')
    const { data } = await this.client.post('/api/v1/kaspi/tokens', {
      store_name,
      token,
    })
    return data // { store_name }
  }

  async kaspiListTokens() {
    const { data } = await this.client.get('/api/v1/kaspi/tokens')
    return data // [{ store_name }, ...]
  }

  async kaspiGetTokenMasked(store_name) {
    if (!store_name) throw new Error('store_name is required')
    const { data } = await this.client.get(`/api/v1/kaspi/tokens/${encodeURIComponent(store_name)}`)
    return data // { id, store_name, token_hex_masked, created_at, updated_at }
  }

  async kaspiDeleteToken(store_name) {
    if (!store_name) throw new Error('store_name is required')
    const { data } = await this.client.delete(`/api/v1/kaspi/tokens/${encodeURIComponent(store_name)}`)
    return data // usually empty (204)
  }

  // -------------------- Kaspi: Ops via Adapter --------------------
  async kaspiHealth(store) {
    if (!store) throw new Error('store is required')
    const { data } = await this.client.get(`/api/v1/kaspi/health/${encodeURIComponent(store)}`)
    return data
  }

  async kaspiOrders({ store, state }) {
    if (!store) throw new Error('store is required')
    const { data } = await this.client.post('/api/v1/kaspi/orders', { store, state })
    return data
  }

  async kaspiImport({ store, offers_json_path }) {
    if (!store || !offers_json_path) {
      throw new Error('store and offers_json_path are required')
    }
    const { data } = await this.client.post('/api/v1/kaspi/import', {
      store,
      offers_json_path,
    })
    return data // { import_id, ... } (как вернёт адаптер)
  }

  async kaspiImportStatus({ store, import_id }) {
    if (!store || !import_id) throw new Error('store and import_id are required')
    const { data } = await this.client.post('/api/v1/kaspi/import/status', {
      store,
      import_id,
    })
    return data
  }

  // -------------------- Kaspi: Service (DB) --------------------
  async kaspiOrdersSync({ company_id }) {
    if (!company_id) throw new Error('company_id is required')
    const { data } = await this.client.post('/api/v1/kaspi/orders/sync', { company_id })
    return data // результат sync-а
  }

  /**
   * Генерация XML-фида (возвращает строку XML).
   * Если нужно сохранить локально из браузера — используйте { download: true, filename }.
   */
  async kaspiGenerateFeed(company_id, { download = false, filename = 'kaspi-feed.xml' } = {}) {
    if (!company_id) throw new Error('company_id is required')
    const res = await this.client.get(`/api/v1/kaspi/feed/${company_id}`, {
      responseType: 'text',
      transformResponse: (x) => x, // не парсим XML
      headers: { Accept: 'application/xml' },
    })
    const xml = res?.data ?? ''
    if (download) {
      downloadBlob(xml, filename, 'application/xml')
    }
    return xml
  }

  async kaspiAvailabilitySyncOne({ product_id }) {
    if (!product_id) throw new Error('product_id is required')
    const { data } = await this.client.post('/api/v1/kaspi/availability/sync', { product_id })
    return data // { ok: true/false }
  }

  async kaspiAvailabilityBulk({ company_id, limit = 500 }) {
    if (!company_id) throw new Error('company_id is required')
    const { data } = await this.client.post('/api/v1/kaspi/availability/bulk', {
      company_id,
      limit,
    })
    return data // stats
  }

  // -------------------- Misc / Utilities --------------------
  /** Возможность задать/поменять базовый URL на лету (например, из настроек UI) */
  setBaseURL(url) {
    const next = normalizeBase(url)
    this.baseURL = next
    this.client.defaults.baseURL = next
  }

  /** Создать AbortController-совместимый GET запрос (для живого поиска и т.п.) */
  async cancellableGet(path, { params, signal } = {}) {
    const res = await this.client.get(path + (params ? qs(params) : ''), {
      signal,
    })
    return res.data
  }
}

// --------------- Singleton instance + exports ---------------
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

  kaspiConnect,
  kaspiUpsertToken,
  kaspiListTokens,
  kaspiGetTokenMasked,
  kaspiDeleteToken,

  kaspiHealth,
  kaspiOrders,
  kaspiImport,
  kaspiImportStatus,

  kaspiOrdersSync,
  kaspiGenerateFeed,
  kaspiAvailabilitySyncOne,
  kaspiAvailabilityBulk,

  setBaseURL,
  cancellableGet,
} = {
  healthCheck: (...a) => apiClient.healthCheck(...a),
  getCampaigns: (...a) => apiClient.getCampaigns(...a),
  getCampaign: (...a) => apiClient.getCampaign(...a),
  createCampaign: (...a) => apiClient.createCampaign(...a),
  updateCampaign: (...a) => apiClient.updateCampaign(...a),
  deleteCampaign: (...a) => apiClient.deleteCampaign(...a),
  listCampaignMessages: (...a) => apiClient.listCampaignMessages(...a),
  createCampaignMessage: (...a) => apiClient.createCampaignMessage(...a),

  kaspiConnect: (...a) => apiClient.kaspiConnect(...a),
  kaspiUpsertToken: (...a) => apiClient.kaspiUpsertToken(...a),
  kaspiListTokens: (...a) => apiClient.kaspiListTokens(...a),
  kaspiGetTokenMasked: (...a) => apiClient.kaspiGetTokenMasked(...a),
  kaspiDeleteToken: (...a) => apiClient.kaspiDeleteToken(...a),

  kaspiHealth: (...a) => apiClient.kaspiHealth(...a),
  kaspiOrders: (...a) => apiClient.kaspiOrders(...a),
  kaspiImport: (...a) => apiClient.kaspiImport(...a),
  kaspiImportStatus: (...a) => apiClient.kaspiImportStatus(...a),

  kaspiOrdersSync: (...a) => apiClient.kaspiOrdersSync(...a),
  kaspiGenerateFeed: (...a) => apiClient.kaspiGenerateFeed(...a),
  kaspiAvailabilitySyncOne: (...a) => apiClient.kaspiAvailabilitySyncOne(...a),
  kaspiAvailabilityBulk: (...a) => apiClient.kaspiAvailabilityBulk(...a),

  setBaseURL: (...a) => apiClient.setBaseURL(...a),
  cancellableGet: (...a) => apiClient.cancellableGet(...a),
}
