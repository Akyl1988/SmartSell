import { apiClient } from './client'

export type HealthOut = {
  ok: boolean
  engine?: string | null
  error?: string | null
}

export type StatsOut = {
  accounts: number
  ledger_entries: number
  total_balance: string
}

export type WalletAccountOut = {
  id: number
  user_id: number
  currency: string
  balance: string
  created_at: string
  updated_at: string
}

export type WalletAccountCreate = {
  user_id: number
  currency: string
  balance?: number | string | null
}

export type WalletAccountsPage = {
  items: WalletAccountOut[]
  meta: { page: number; size: number; total: number }
}

export type BalanceOut = {
  account_id: number
  currency: string
  balance: string
}

export type LedgerItem = {
  id: number
  account_id: number
  type: string
  amount: string
  currency: string
  reference: string | null
  created_at: string
}

export type LedgerPage = {
  items: LedgerItem[]
  meta: { page: number; size: number; total: number }
}

export type WalletDeposit = {
  amount: number | string
  reference?: string | null
}

export type WalletWithdraw = {
  amount: number | string
  reference?: string | null
}

export type WalletTransfer = {
  source_account_id: number
  destination_account_id: number
  amount: number | string
  reference?: string | null
}

export type WalletTxBalance = {
  account_id: number
  currency: string
  balance: string
}

export type WalletTransferOut = {
  source: WalletTxBalance
  destination: WalletTxBalance
}

export type WalletTransactionOut = {
  account_id: number
  currency: string
  balance: string
}

export type AdjustIn = {
  new_balance: number | string
  reference?: string | null
}

export type WalletAccountListParams = {
  user_id?: number | null
  currency?: string | null
  page?: number
  size?: number
}

export async function getWalletHealth(): Promise<HealthOut> {
  const { data } = await apiClient.get<HealthOut>('/api/v1/wallet/health')
  return data
}

export async function getWalletStats(): Promise<StatsOut> {
  const { data } = await apiClient.get<StatsOut>('/api/v1/wallet/stats')
  return data
}

export async function listWalletAccounts(params: WalletAccountListParams = {}): Promise<WalletAccountsPage> {
  const { data } = await apiClient.get<WalletAccountsPage>('/api/v1/wallet/accounts', { params })
  return data
}

export async function createWalletAccount(payload: WalletAccountCreate): Promise<WalletAccountOut> {
  const { data } = await apiClient.post<WalletAccountOut>('/api/v1/wallet/accounts', payload)
  return data
}

export async function getWalletAccountByUser(user_id: number, currency: string): Promise<WalletAccountOut> {
  const { data } = await apiClient.get<WalletAccountOut>('/api/v1/wallet/accounts/by-user', {
    params: { user_id, currency },
  })
  return data
}

export async function getWalletAccount(accountId: number): Promise<WalletAccountOut> {
  const { data } = await apiClient.get<WalletAccountOut>(`/api/v1/wallet/accounts/${accountId}`)
  return data
}

export async function getWalletBalance(accountId: number): Promise<BalanceOut> {
  const { data } = await apiClient.get<BalanceOut>(`/api/v1/wallet/accounts/${accountId}/balance`)
  return data
}

export async function getWalletLedger(accountId: number, page?: number, size?: number): Promise<LedgerPage> {
  const { data } = await apiClient.get<LedgerPage>(`/api/v1/wallet/accounts/${accountId}/ledger`, {
    params: { page, size },
  })
  return data
}

export async function depositToWallet(accountId: number, payload: WalletDeposit): Promise<WalletTransactionOut> {
  const { data } = await apiClient.post<WalletTransactionOut>(
    `/api/v1/wallet/accounts/${accountId}/deposit`,
    payload
  )
  return data
}

export async function withdrawFromWallet(accountId: number, payload: WalletWithdraw): Promise<WalletTransactionOut> {
  const { data } = await apiClient.post<WalletTransactionOut>(
    `/api/v1/wallet/accounts/${accountId}/withdraw`,
    payload
  )
  return data
}

export async function transferWallet(payload: WalletTransfer): Promise<WalletTransferOut> {
  const { data } = await apiClient.post<WalletTransferOut>('/api/v1/wallet/transfer', payload)
  return data
}

export async function adjustWalletBalance(accountId: number, payload: AdjustIn): Promise<WalletTransactionOut> {
  const { data } = await apiClient.post<WalletTransactionOut>(
    `/api/v1/wallet/accounts/${accountId}/adjust`,
    payload
  )
  return data
}
