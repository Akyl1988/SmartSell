import { apiClient } from './client'

export type PlatformHealth = {
  db_ok: boolean
  redis_ok: boolean
  worker_ok: boolean
}

export type PlatformSummary = {
  companies_total: number
  companies_active: number
  stores_with_kaspi_connected: number
  subscriptions: {
    total: number
    by_plan: {
      free: number
      trial: number
      pro: number
    }
  }
  wallet: {
    total_balance: string | number
    active_wallets: number
  }
  health: PlatformHealth
}

export type CompanyListItem = {
  id: number
  name: string
  bin_iin?: string | null
  created_at: string
  is_active: boolean
  kaspi_store_id?: string | null
  current_plan?: string | null
  plan_expires_at?: string | null
}

export type CompaniesPage = {
  items: CompanyListItem[]
  page: number
  size: number
  total: number
}

export type CompanyAdmin = {
  phone?: string | null
  role: string
  is_active: boolean
}

export type CompanyDetail = {
  id: number
  name: string
  bin_iin?: string | null
  created_at: string
  is_active: boolean
  kaspi_store_id?: string | null
  current_plan?: string | null
  plan_expires_at?: string | null
  admins: CompanyAdmin[]
}

export type AdminInviteRequest = {
  company_id: number
  phone: string
  grace_days?: number
  initial_plan?: 'trial_pro' | 'free' | 'pro'
}

export type AdminInviteResponse = {
  invite_url: string
  otp_grace_until: string | null
  company_id: number
}

export type SubscriptionStoreRow = {
  company_id: number
  company_name: string
  plan: string
  status: string
  current_period_start?: string | null
  current_period_end?: string | null
  wallet_balance: string | number
}

export type SubscriptionSetPlanRequest = {
  plan: string
  reason: string
}

export type SubscriptionExtendRequest = {
  days: number
  reason: string
}

export type SubscriptionAdminOut = {
  id: number
  company_id: number
  plan: string
  status: string
  billing_cycle: string
  price: string | number
  currency: string
  started_at?: string | null
  period_start?: string | null
  period_end?: string | null
  next_billing_date?: string | null
  grace_until?: string | null
  billing_anchor_day?: number | null
}

export async function getPlatformSummary(): Promise<PlatformSummary> {
  const { data } = await apiClient.get<PlatformSummary>('/api/v1/admin/platform/summary')
  return data
}

export async function getCompanies(params: {
  page: number
  size: number
  q?: string
}): Promise<CompaniesPage> {
  const { page, size, q } = params
  const { data } = await apiClient.get<CompaniesPage>('/api/v1/admin/companies', {
    params: { page, size, q: q || undefined },
  })
  return data
}

export async function getCompanyDetail(companyId: number): Promise<CompanyDetail> {
  const { data } = await apiClient.get<CompanyDetail>(`/api/v1/admin/companies/${companyId}`)
  return data
}

export async function createAdminInvite(payload: AdminInviteRequest): Promise<AdminInviteResponse> {
  const { data } = await apiClient.post<AdminInviteResponse>('/api/v1/admin/invites', payload)
  return data
}

export async function getSubscriptionStores(): Promise<SubscriptionStoreRow[]> {
  const { data } = await apiClient.get<SubscriptionStoreRow[]>('/api/v1/admin/subscriptions/stores')
  return data
}

export async function setSubscriptionPlan(
  companyId: number,
  payload: SubscriptionSetPlanRequest
): Promise<SubscriptionAdminOut> {
  const { data } = await apiClient.post<SubscriptionAdminOut>(
    `/api/v1/admin/subscriptions/${companyId}/set-plan`,
    payload
  )
  return data
}

export async function extendSubscription(
  companyId: number,
  payload: SubscriptionExtendRequest
): Promise<SubscriptionAdminOut> {
  const { data } = await apiClient.post<SubscriptionAdminOut>(
    `/api/v1/admin/subscriptions/${companyId}/extend`,
    payload
  )
  return data
}

export async function runSubscriptionRenew(): Promise<{ ok: boolean; processed: number; request_id?: string }> {
  const { data } = await apiClient.post<{ ok: boolean; processed: number; request_id?: string }>(
    '/api/v1/admin/tasks/subscriptions/renew/run'
  )
  return data
}
