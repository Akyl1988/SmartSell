import { apiClient } from './client'

export type SalesAnalytics = {
  labels: string[]
  data: string[]
  total: string
  average: string
  growth_rate?: number | null
}

export type DashboardStats = {
  total_orders: number
  total_revenue: string
  total_products: number
  total_customers: number
  pending_orders: number
  low_stock_alerts: number
  recent_orders: Record<string, unknown>[]
  sales_chart: SalesAnalytics
  top_products: Record<string, unknown>[]
}

export type CustomerAnalytics = {
  total_customers: number
  new_customers: number
  repeat_customers: number
  repeat_rate: number
  average_orders_per_customer: number
  top_customers: Record<string, unknown>[]
}

export type ProductAnalytics = {
  top_products: Record<string, unknown>[]
  low_stock_products: Record<string, unknown>[]
  out_of_stock_products: Record<string, unknown>[]
  category_performance: Record<string, unknown>[]
}

export type ExportRequest = {
  export_type: string
  format?: string
  date_from?: string | null
  date_to?: string | null
  filters?: Record<string, unknown> | null
}

export type AnalyticsQuery = {
  date_from?: string | null
  date_to?: string | null
  interval?: 'day' | 'week' | 'month'
  warehouse_id?: number | null
  category?: string | null
  product_id?: number | null
}

export async function getDashboardStats(): Promise<DashboardStats> {
  const { data } = await apiClient.get<DashboardStats>('/api/v1/analytics/dashboard')
  return data
}

export async function getSalesAnalytics(params: AnalyticsQuery = {}): Promise<SalesAnalytics> {
  const { data } = await apiClient.get<SalesAnalytics>('/api/v1/analytics/sales', { params })
  return data
}

export async function getCustomerAnalytics(params: AnalyticsQuery = {}): Promise<CustomerAnalytics> {
  const { data } = await apiClient.get<CustomerAnalytics>('/api/v1/analytics/customers', { params })
  return data
}

export async function getProductAnalytics(params: AnalyticsQuery = {}): Promise<ProductAnalytics> {
  const { data } = await apiClient.get<ProductAnalytics>('/api/v1/analytics/products', { params })
  return data
}

export async function exportAnalytics(payload: ExportRequest): Promise<Blob> {
  const { data } = await apiClient.post<Blob>('/api/v1/analytics/export', payload, {
    responseType: 'blob',
  })
  return data
}
