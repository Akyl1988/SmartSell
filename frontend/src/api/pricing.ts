import { apiClient } from './client'

export type PaginatedResponse<T> = {
  items: T[]
  total: number
  page: number
  per_page: number
  pages: number
  has_next: boolean
  has_prev: boolean
}

export type PricingRuleCreate = {
  name: string
  is_active?: boolean
  enabled?: boolean
  scope?: Record<string, unknown> | null
  min_price?: number | string | null
  max_price?: number | string | null
  step?: number | string | null
  undercut?: number | string | null
  cooldown_seconds?: number | null
  max_delta_percent?: number | string | null
}

export type PricingRuleUpdate = {
  name?: string | null
  is_active?: boolean | null
  enabled?: boolean | null
  scope?: Record<string, unknown> | null
  min_price?: number | string | null
  max_price?: number | string | null
  step?: number | string | null
  undercut?: number | string | null
  cooldown_seconds?: number | null
  max_delta_percent?: number | string | null
}

export type PricingRuleResponse = {
  id: number
  created_at: string
  updated_at: string
  name: string
  is_active?: boolean
  enabled?: boolean
  scope?: Record<string, unknown> | null
  min_price?: string | null
  max_price?: string | null
  step?: string | null
  undercut?: string | null
  cooldown_seconds?: number | null
  max_delta_percent?: string | null
  company_id: number
}

export type PricingProductFilter = {
  product_ids?: number[] | null
  category_id?: number | null
  sku?: string | null
  name_contains?: string | null
  min_price?: number | string | null
  max_price?: number | string | null
  is_active?: boolean | null
  limit?: number | null
}

export type PricingRuleInline = {
  min_price?: number | string | null
  max_price?: number | string | null
  step?: number | string | null
  undercut?: number | string | null
  cooldown_seconds?: number | null
  max_delta_percent?: number | string | null
}

export type PricingPreviewRequest = {
  rule_id?: number | null
  rule?: PricingRuleInline | null
  filters?: PricingProductFilter | null
}

export type PricingPreviewItem = {
  product_id: number
  old_price: string | null
  new_price: string | null
  reason: string
}

export type PricingApplyRequest = {
  rule_id: number
  filters?: PricingProductFilter | null
}

export type PricingApplyResponse = {
  run_id: number
  stats: Record<string, unknown>
  diffs: PricingPreviewItem[]
}

export type RepricingRuleCreate = {
  name: string
  enabled?: boolean
  scope_type?: string
  scope_value?: string | null
  min_price?: number | string | null
  max_price?: number | string | null
  step?: number | string | null
  rounding_mode?: string | null
  is_active?: boolean
}

export type RepricingRuleUpdate = {
  name?: string | null
  enabled?: boolean | null
  is_active?: boolean | null
  scope_type?: string | null
  scope_value?: string | null
  min_price?: number | string | null
  max_price?: number | string | null
  step?: number | string | null
  rounding_mode?: string | null
}

export type RepricingRuleResponse = {
  id: number
  created_at: string
  updated_at: string
  name: string
  enabled?: boolean
  scope_type?: string
  scope_value?: string | null
  min_price?: string | null
  max_price?: string | null
  step?: string | null
  rounding_mode?: string | null
  company_id: number
  is_active: boolean
}

export type RepricingRunItemResponse = {
  id: number
  created_at: string
  updated_at: string
  run_id: number
  product_id: number | null
  old_price: string | null
  new_price: string | null
  reason: string | null
  status: string | null
  error: string | null
}

export type RepricingRunResponse = {
  id: number
  created_at: string
  updated_at: string
  company_id: number
  status: string
  started_at: string | null
  finished_at: string | null
  processed: number | null
  changed: number | null
  failed: number | null
  last_error: string | null
  request_id: string | null
  triggered_by_user_id: number | null
  items?: RepricingRunItemResponse[] | null
}

export type RepricingRunTriggerResponse = {
  run_id: number
}

export type PricingListParams = {
  include_inactive?: boolean
  page?: number
  per_page?: number
}

export type RepricingListParams = {
  include_inactive?: boolean
  page?: number
  per_page?: number
}

export type RepricingRunsParams = {
  page?: number
  per_page?: number
}

export async function listPricingRules(params: PricingListParams = {}): Promise<PaginatedResponse<PricingRuleResponse>> {
  const { data } = await apiClient.get<PaginatedResponse<PricingRuleResponse>>('/api/v1/pricing/rules', { params })
  return data
}

export async function getPricingRule(ruleId: number): Promise<PricingRuleResponse> {
  const { data } = await apiClient.get<PricingRuleResponse>(`/api/v1/pricing/rules/${ruleId}`)
  return data
}

export async function createPricingRule(payload: PricingRuleCreate): Promise<PricingRuleResponse> {
  const { data } = await apiClient.post<PricingRuleResponse>('/api/v1/pricing/rules', payload)
  return data
}

export async function updatePricingRule(ruleId: number, payload: PricingRuleUpdate): Promise<PricingRuleResponse> {
  const { data } = await apiClient.patch<PricingRuleResponse>(`/api/v1/pricing/rules/${ruleId}`, payload)
  return data
}

export async function deletePricingRule(ruleId: number): Promise<{ message: string; data?: Record<string, unknown> | null }> {
  const { data } = await apiClient.delete<{ message: string; data?: Record<string, unknown> | null }>(
    `/api/v1/pricing/rules/${ruleId}`
  )
  return data
}

export async function previewPricing(payload: PricingPreviewRequest): Promise<PricingPreviewItem[]> {
  const { data } = await apiClient.post<PricingPreviewItem[]>('/api/v1/pricing/preview', payload)
  return data
}

export async function applyPricing(payload: PricingApplyRequest): Promise<PricingApplyResponse> {
  const { data } = await apiClient.post<PricingApplyResponse>('/api/v1/pricing/apply', payload)
  return data
}

export async function listRepricingRules(params: RepricingListParams = {}): Promise<PaginatedResponse<RepricingRuleResponse>> {
  const { data } = await apiClient.get<PaginatedResponse<RepricingRuleResponse>>('/api/v1/repricing/rules', { params })
  return data
}

export async function getRepricingRule(ruleId: number): Promise<RepricingRuleResponse> {
  const { data } = await apiClient.get<RepricingRuleResponse>(`/api/v1/repricing/rules/${ruleId}`)
  return data
}

export async function createRepricingRule(payload: RepricingRuleCreate): Promise<RepricingRuleResponse> {
  const { data } = await apiClient.post<RepricingRuleResponse>('/api/v1/repricing/rules', payload)
  return data
}

export async function updateRepricingRule(ruleId: number, payload: RepricingRuleUpdate): Promise<RepricingRuleResponse> {
  const { data } = await apiClient.patch<RepricingRuleResponse>(`/api/v1/repricing/rules/${ruleId}`, payload)
  return data
}

export async function deleteRepricingRule(ruleId: number): Promise<{ message: string; data?: Record<string, unknown> | null }> {
  const { data } = await apiClient.delete<{ message: string; data?: Record<string, unknown> | null }>(
    `/api/v1/repricing/rules/${ruleId}`
  )
  return data
}

export async function runRepricing(dryRun = false): Promise<RepricingRunTriggerResponse> {
  const { data } = await apiClient.post<RepricingRunTriggerResponse>('/api/v1/repricing/run', null, {
    params: { dry_run: dryRun },
  })
  return data
}

export async function listRepricingRuns(params: RepricingRunsParams = {}): Promise<RepricingRunResponse[]> {
  const { data } = await apiClient.get<PaginatedResponse<RepricingRunResponse>>('/api/v1/repricing/runs', { params })
  return data.items
}

export async function getRepricingRun(runId: number): Promise<RepricingRunResponse> {
  const { data } = await apiClient.get<RepricingRunResponse>(`/api/v1/repricing/runs/${runId}`)
  return data
}

export async function applyRepricingRun(runId: number, dryRun = false): Promise<RepricingRunResponse> {
  const { data } = await apiClient.post<RepricingRunResponse>(`/api/v1/repricing/runs/${runId}/apply`, null, {
    params: { dry_run: dryRun },
  })
  return data
}
