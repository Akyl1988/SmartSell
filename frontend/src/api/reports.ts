import { apiClient } from './client'

export type CsvReportParams = {
  limit?: number
  date_from?: string | null
  date_to?: string | null
  companyId?: number | null
}

export type InventoryReportParams = {
  limit?: number
  warehouseId?: number | null
  companyId?: number | null
}

export type RepricingRunsReportParams = {
  limit?: number
  date_from?: string | null
  date_to?: string | null
  status?: string | null
  companyId?: number | null
}

export type OrdersExportParams = {
  date_from?: string | null
  date_to?: string | null
  limit?: number
}

export type ProductsExportParams = {
  limit?: number
}

async function downloadBlob(path: string, params?: Record<string, unknown>): Promise<Blob> {
  const { data } = await apiClient.get<Blob>(path, { params, responseType: 'blob' })
  return data
}

export async function downloadWalletTransactionsReport(params: CsvReportParams = {}): Promise<Blob> {
  return downloadBlob('/api/v1/reports/wallet/transactions.csv', params)
}

export async function downloadOrdersReport(params: CsvReportParams = {}): Promise<Blob> {
  return downloadBlob('/api/v1/reports/orders.csv', params)
}

export async function downloadOrderItemsReport(params: CsvReportParams = {}): Promise<Blob> {
  return downloadBlob('/api/v1/reports/order_items.csv', params)
}

export async function downloadPreordersReport(params: CsvReportParams = {}): Promise<Blob> {
  return downloadBlob('/api/v1/reports/preorders.csv', params)
}

export async function downloadInventoryReport(params: InventoryReportParams = {}): Promise<Blob> {
  return downloadBlob('/api/v1/reports/inventory.csv', params)
}

export async function downloadRepricingRunsReport(params: RepricingRunsReportParams = {}): Promise<Blob> {
  return downloadBlob('/api/v1/reports/repricing_runs.csv', params)
}

export async function exportOrdersXlsx(params: OrdersExportParams = {}): Promise<Blob> {
  return downloadBlob('/api/v1/exports/orders.xlsx', params)
}

export async function exportSalesXlsx(params: OrdersExportParams = {}): Promise<Blob> {
  return downloadBlob('/api/v1/exports/sales.xlsx', params)
}

export async function exportProductsXlsx(params: ProductsExportParams = {}): Promise<Blob> {
  return downloadBlob('/api/v1/exports/products.xlsx', params)
}
