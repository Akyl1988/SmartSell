import { useEffect, useMemo, useState } from 'react'
import { hasSessionToken } from '../auth/tokenStore'
import { getCurrentSubscription, SubscriptionOut } from '../api/subscriptions'

export type FeatureGate = {
  hasPreorders: boolean
  hasRepricing: boolean
  plan: string | null
  loading: boolean
  paymentRequired: boolean
}

const PRO_KEYWORD = 'pro'

function isProPlan(plan: string | null) {
  return plan ? plan.toLowerCase().includes(PRO_KEYWORD) : false
}

export function useFeatureGate(): FeatureGate {
  const [subscription, setSubscription] = useState<SubscriptionOut | null>(null)
  const [loading, setLoading] = useState(true)
  const [paymentRequired, setPaymentRequired] = useState(false)

  useEffect(() => {
    if (!hasSessionToken()) {
      setLoading(false)
      return
    }

    let mounted = true
    getCurrentSubscription()
      .then((data) => {
        if (mounted) {
          setSubscription(data)
        }
      })
      .catch(() => {
        if (mounted) {
          setSubscription(null)
        }
      })
      .finally(() => {
        if (mounted) {
          setLoading(false)
        }
      })

    return () => {
      mounted = false
    }
  }, [])

  useEffect(() => {
    const onPaymentRequired = () => setPaymentRequired(true)
    window.addEventListener('auth:payment_required', onPaymentRequired)
    return () => window.removeEventListener('auth:payment_required', onPaymentRequired)
  }, [])

  const plan = subscription?.plan ?? null
  const hasPro = useMemo(() => isProPlan(plan), [plan])

  return {
    hasPreorders: hasPro,
    hasRepricing: hasPro,
    plan,
    loading,
    paymentRequired,
  }
}
