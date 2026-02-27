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

export type PreorderItemIn = {
  product_id?: number | null
  sku?: string | null
  name?: string | null
  qty: number
  price?: number | string | null
}

export type PreorderCreateIn = {
  currency?: string
  customer_name?: string | null
  customer_phone?: string | null
  notes?: string | null
  items?: PreorderItemIn[]
}

export type PreorderUpdateIn = {
  customer_name?: string | null
  customer_phone?: string | null
  notes?: string | null
  items?: PreorderItemIn[] | null
}

export type PreorderItemOut = {
  id: number
  created_at: string
  updated_at: string
  product_id?: number | null
  sku?: string | null
  name?: string | null
  qty: number
  price?: string | null
  preorder_id: number
}

export type Preorder = {
  id: number
  created_at: string
  updated_at: string
  company_id: number
  status: string
  currency: string
  total: string | null
  customer_name: string | null
  customer_phone: string | null
  notes: string | null
  created_by_user_id: number | null
  fulfilled_order_id: number | null
  fulfilled_at: string | null
  items?: PreorderItemOut[] | null
}

export type PreorderListParams = {
  status?: string | null
  date_from?: string | null
  date_to?: string | null
  page?: number
  per_page?: number
}

export async function listPreorders(
  params: PreorderListParams = {}
): Promise<PaginatedResponse<Preorder>> {
  const { data } = await apiClient.get<PaginatedResponse<Preorder>>('/api/v1/preorders', { params })
  return data
}

export async function getPreorderById(preorderId: number): Promise<Preorder> {
  const { data } = await apiClient.get<Preorder>(`/api/v1/preorders/${preorderId}`)
  return data
}

export async function createPreorder(payload: PreorderCreateIn): Promise<Preorder> {
  const { data } = await apiClient.post<Preorder>('/api/v1/preorders', payload)
  return data
}

export async function updatePreorder(preorderId: number, payload: PreorderUpdateIn): Promise<Preorder> {
  const { data } = await apiClient.patch<Preorder>(`/api/v1/preorders/${preorderId}`, payload)
  return data
}

export async function confirmPreorder(preorderId: number): Promise<Preorder> {
  const { data } = await apiClient.post<Preorder>(`/api/v1/preorders/${preorderId}/confirm`)
  return data
}

export async function cancelPreorder(preorderId: number): Promise<Preorder> {
  const { data } = await apiClient.post<Preorder>(`/api/v1/preorders/${preorderId}/cancel`)
  return data
}

export async function fulfillPreorder(preorderId: number): Promise<Preorder> {
  const { data } = await apiClient.post<Preorder>(`/api/v1/preorders/${preorderId}/fulfill`)
  return data
}
