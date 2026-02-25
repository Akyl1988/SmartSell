import { apiClient } from './client'

export type SuccessResponse = {
  message: string
  data?: Record<string, unknown> | null
}

export type PaginatedResponse<T> = {
  items: T[]
  total: number
  page: number
  per_page: number
  pages: number
  has_next: boolean
  has_prev: boolean
}

// OpenAPI returns categories as untyped objects; this is a minimal assumed shape.
export type CategoryResponse = {
  id: number
  created_at: string
  updated_at: string
  name: string
  slug: string
  description?: string | null
  parent_id?: number | null
  is_active?: boolean
  sort_order?: number
}

export type ProductResponse = {
  id: number
  created_at: string
  updated_at: string
  name: string
  slug: string
  sku: string
  price: string
  description?: string | null
  short_description?: string | null
  cost_price?: string | null
  sale_price?: string | null
  stock_quantity?: number
  min_stock_level?: number
  max_stock_level?: number | null
  is_active?: boolean
  is_featured?: boolean
  is_digital?: boolean
  meta_title?: string | null
  meta_description?: string | null
  meta_keywords?: string | null
  category_id?: number | null
  image_url?: string | null
  gallery_urls?: string[] | null
  weight?: string | null
  length?: string | null
  width?: string | null
  height?: string | null
  is_preorder_enabled: boolean
  preorder_until?: number | null
  preorder_deposit?: string | null
  preorder_note?: string | null
  preorder_lead_days?: number | null
  preorder_show_zero_stock?: boolean | null
  category?: CategoryResponse | null
}

export type ProductCreate = {
  name: string
  slug: string
  sku: string
  price: number | string
  description?: string | null
  short_description?: string | null
  cost_price?: number | string | null
  sale_price?: number | string | null
  stock_quantity?: number
  min_stock_level?: number
  max_stock_level?: number | null
  is_active?: boolean
  is_featured?: boolean
  is_digital?: boolean
  meta_title?: string | null
  meta_description?: string | null
  meta_keywords?: string | null
  category_id?: number | null
  image_url?: string | null
  gallery_urls?: string[] | null
  weight?: number | string | null
  length?: number | string | null
  width?: number | string | null
  height?: number | string | null
}

export type ProductUpdate = {
  name?: string | null
  slug?: string | null
  description?: string | null
  short_description?: string | null
  price?: number | string | null
  cost_price?: number | string | null
  sale_price?: number | string | null
  stock_quantity?: number | null
  min_stock_level?: number | null
  max_stock_level?: number | null
  is_active?: boolean | null
  is_featured?: boolean | null
  is_digital?: boolean | null
  is_preorder_enabled?: boolean | null
  preorder_until?: number | null
  preorder_deposit?: number | string | null
  preorder_note?: string | null
  preorder_lead_days?: number | null
  preorder_show_zero_stock?: boolean | null
  meta_title?: string | null
  meta_description?: string | null
  meta_keywords?: string | null
  category_id?: number | null
  image_url?: string | null
  gallery_urls?: string[] | null
  weight?: number | string | null
  length?: number | string | null
  width?: number | string | null
  height?: number | string | null
}

export type RepricingConfigIn = {
  enabled?: boolean
  min?: number | null
  max?: number | null
  step?: number
  channel?: 'kaspi' | 'all'
  friendly_ids?: string[]
  cooldown?: number
  hysteresis?: number
}

export type RepricingConfigOut = {
  enabled?: boolean
  min?: number | null
  max?: number | null
  step?: number
  channel?: 'kaspi' | 'all'
  friendly_ids?: string[]
  cooldown?: number
  hysteresis?: number
}

export type RepricingTickOut = {
  current_price: number | null
  target_price: number | null
  best_competitor: Record<string, unknown> | null
  reason: string
  applied?: boolean
}

export type ProductListParams = {
  sort_by?: string
  sort_order?: 'asc' | 'desc'
  category_id?: number | null
  min_price?: number | string | null
  max_price?: number | string | null
  is_active?: boolean | null
  is_featured?: boolean | null
  is_digital?: boolean | null
  in_stock?: boolean | null
  search?: string | null
  page?: number
  per_page?: number
}

export async function listProducts(params: ProductListParams = {}): Promise<ProductResponse[]> {
  const { data } = await apiClient.get<PaginatedResponse<ProductResponse>>('/api/v1/products', { params })
  return data.items
}

export async function fetchProductById(productId: number): Promise<ProductResponse> {
  const { data } = await apiClient.get<ProductResponse>(`/api/v1/products/${productId}`)
  return data
}

export async function createProduct(payload: ProductCreate): Promise<ProductResponse> {
  const { data } = await apiClient.post<ProductResponse>('/api/v1/products', payload)
  return data
}

export async function updateProduct(productId: number, payload: ProductUpdate): Promise<ProductResponse> {
  const { data } = await apiClient.put<ProductResponse>(`/api/v1/products/${productId}`, payload)
  return data
}

export async function deleteProduct(productId: number): Promise<SuccessResponse> {
  const { data } = await apiClient.delete<SuccessResponse>(`/api/v1/products/${productId}`)
  return data
}

export async function updateProductStock(productId: number, stockQuantity: number): Promise<SuccessResponse> {
  const { data } = await apiClient.put<SuccessResponse>(
    `/api/v1/products/${productId}/stock`,
    null,
    { params: { stock_quantity: stockQuantity } }
  )
  return data
}

export async function getProductStock(productId: number): Promise<Record<string, unknown>> {
  // OpenAPI defines this response as an untyped object.
  const { data } = await apiClient.get<Record<string, unknown>>(`/api/v1/products/${productId}/stock`)
  return data
}

export async function setProductFeatured(productId: number, featured: boolean): Promise<SuccessResponse> {
  const { data } = await apiClient.post<SuccessResponse>(
    `/api/v1/products/${productId}/feature`,
    null,
    { params: { featured } }
  )
  return data
}

export async function activateProduct(productId: number): Promise<SuccessResponse> {
  const { data } = await apiClient.post<SuccessResponse>(`/api/v1/products/${productId}/activate`)
  return data
}

export async function deactivateProduct(productId: number): Promise<SuccessResponse> {
  const { data } = await apiClient.post<SuccessResponse>(`/api/v1/products/${productId}/deactivate`)
  return data
}

export async function setRepricingConfig(productId: number, payload: RepricingConfigIn): Promise<RepricingConfigOut> {
  const { data } = await apiClient.put<RepricingConfigOut>(
    `/api/v1/products/${productId}/repricing/config`,
    payload
  )
  return data
}

export async function repricingTick(productId: number, apply = false): Promise<RepricingTickOut> {
  const { data } = await apiClient.post<RepricingTickOut>(
    `/api/v1/products/${productId}/repricing/tick`,
    null,
    { params: { apply } }
  )
  return data
}
