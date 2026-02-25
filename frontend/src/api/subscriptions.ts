import { apiClient } from './client'

export type PlanCatalogOut = {
  plan_id: string
  plan: string
  currency: string
  monthly_price: string
  yearly_price: string
}

export type SubscriptionOut = {
  id: number
  company_id: number
  plan: string
  status: string
  billing_cycle: string
  price: string
  currency: string
  started_at: string | null
  expires_at: string | null
  next_billing_date: string | null
  canceled_at?: string | null
  ended_at?: string | null
  deleted_at?: string | null
  plan_id: string
}

export type SubscriptionCreate = {
  plan: string
  billing_cycle?: 'monthly' | 'yearly'
  price?: number | string
  currency?: string
  trial_days?: number
}

export type SubscriptionUpdate = {
  plan?: string | null
  billing_cycle?: 'monthly' | 'yearly' | null
  price?: number | string | null
  currency?: string | null
}

export type PaymentOut = {
  id: number
  provider?: string | null
  status?: string | null
  amount?: string | null
  currency?: string | null
  created_at?: string | null
}

export type ListSubscriptionsParams = {
  status_filter?:
    | 'active'
    | 'trialing'
    | 'past_due'
    | 'frozen'
    | 'canceled'
    | 'overdue'
    | 'trial'
    | 'paused'
    | 'expired'
    | 'ended'
    | null
  plan?: string | null
  from_date?: string | null
  to_date?: string | null
  include_deleted?: boolean
}

export async function listPlanCatalog(): Promise<PlanCatalogOut[]> {
  const { data } = await apiClient.get<PlanCatalogOut[]>('/api/v1/subscriptions/plans')
  return data
}

export async function listSubscriptions(params: ListSubscriptionsParams = {}): Promise<SubscriptionOut[]> {
  const { data } = await apiClient.get<SubscriptionOut[]>('/api/v1/subscriptions', { params })
  return data
}

export async function createSubscription(payload: SubscriptionCreate): Promise<SubscriptionOut> {
  const { data } = await apiClient.post<SubscriptionOut>('/api/v1/subscriptions', payload)
  return data
}

export async function getCurrentSubscription(): Promise<SubscriptionOut | null> {
  const { data } = await apiClient.get<SubscriptionOut | null>('/api/v1/subscriptions/current')
  return data
}

export async function getSubscription(subscriptionId: number): Promise<SubscriptionOut> {
  const { data } = await apiClient.get<SubscriptionOut>(`/api/v1/subscriptions/${subscriptionId}`)
  return data
}

export async function updateSubscription(
  subscriptionId: number,
  payload: SubscriptionUpdate
): Promise<SubscriptionOut> {
  const { data } = await apiClient.patch<SubscriptionOut>(
    `/api/v1/subscriptions/${subscriptionId}`,
    payload
  )
  return data
}

export async function cancelSubscription(subscriptionId: number): Promise<SubscriptionOut> {
  const { data } = await apiClient.post<SubscriptionOut>(`/api/v1/subscriptions/${subscriptionId}/cancel`)
  return data
}

export async function resumeSubscription(subscriptionId: number): Promise<SubscriptionOut> {
  const { data } = await apiClient.post<SubscriptionOut>(`/api/v1/subscriptions/${subscriptionId}/resume`)
  return data
}

export async function renewSubscription(subscriptionId: number): Promise<SubscriptionOut> {
  const { data } = await apiClient.post<SubscriptionOut>(`/api/v1/subscriptions/${subscriptionId}/renew`)
  return data
}

export async function endTrial(subscriptionId: number): Promise<SubscriptionOut> {
  const { data } = await apiClient.post<SubscriptionOut>(`/api/v1/subscriptions/${subscriptionId}/end-trial`)
  return data
}

export async function archiveSubscription(subscriptionId: number): Promise<SubscriptionOut> {
  const { data } = await apiClient.post<SubscriptionOut>(`/api/v1/subscriptions/${subscriptionId}/archive`)
  return data
}

export async function restoreSubscription(subscriptionId: number): Promise<SubscriptionOut> {
  const { data } = await apiClient.post<SubscriptionOut>(`/api/v1/subscriptions/${subscriptionId}/restore`)
  return data
}

export async function listSubscriptionPayments(subscriptionId: number): Promise<PaymentOut[]> {
  const { data } = await apiClient.get<PaymentOut[]>(
    `/api/v1/subscriptions/${subscriptionId}/payments`
  )
  return data
}
