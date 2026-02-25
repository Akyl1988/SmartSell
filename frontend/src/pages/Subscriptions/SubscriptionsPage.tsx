import { useEffect, useState } from 'react'
import {
  getCurrentSubscription,
  listPlanCatalog,
  PlanCatalogOut,
  SubscriptionOut,
} from '../../api/subscriptions'

export default function SubscriptionsPage() {
  const [plans, setPlans] = useState<PlanCatalogOut[]>([])
  const [current, setCurrent] = useState<SubscriptionOut | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    listPlanCatalog()
      .then(setPlans)
      .catch(() => setError('Failed to load plan catalog.'))

    getCurrentSubscription()
      .then(setCurrent)
      .catch(() => null)
  }, [])

  return (
    <section>
      <h1>Subscriptions</h1>
      {error && <p style={{ color: '#b91c1c' }}>{error}</p>}
      <p>Current plan: {current?.plan ?? 'None'}</p>
      <ul>
        {plans.map((plan) => (
          <li key={plan.plan_id}>
            {plan.plan} — {plan.monthly_price} {plan.currency} / mo
          </li>
        ))}
      </ul>
    </section>
  )
}
