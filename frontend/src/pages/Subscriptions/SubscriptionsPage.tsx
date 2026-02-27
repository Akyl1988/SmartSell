import { useEffect, useState } from 'react'
import { getHttpErrorInfo } from '../../api/client'
import { getCurrentSubscription, listPlanCatalog, PlanCatalogOut, SubscriptionOut } from '../../api/subscriptions'
import Card from '../../components/ui/Card'
import EmptyState from '../../components/ui/EmptyState'
import ErrorState from '../../components/ui/ErrorState'
import Loader from '../../components/ui/Loader'
import StatusBadge from '../../components/ui/StatusBadge'
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from '../../components/ui/Table'
import pageStyles from '../../styles/page.module.css'

export default function SubscriptionsPage() {
  const [plans, setPlans] = useState<PlanCatalogOut[]>([])
  const [current, setCurrent] = useState<SubscriptionOut | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setLoading(true)
    setError(null)
    Promise.all([listPlanCatalog(), getCurrentSubscription()])
      .then(([catalog, subscription]) => {
        setPlans(catalog)
        setCurrent(subscription)
      })
      .catch((err) => {
        const info = getHttpErrorInfo(err)
        const statusPart = info.status ? ` (status ${info.status})` : ''
        setError(`Failed to load subscriptions${statusPart}: ${info.message}`)
      })
      .finally(() => setLoading(false))
  }, [])

  return (
    <section className={pageStyles.page}>
      <div className={pageStyles.pageHeader}>
        <div>
          <h1 className={pageStyles.pageTitle}>Subscriptions</h1>
          <p className={pageStyles.pageDescription}>Review your current plan and available upgrades.</p>
        </div>
      </div>

      {loading && <Loader label="Loading subscription..." />}
      {error && <ErrorState message={error} />}

      {!loading && !error && (
        <div className={pageStyles.section}>
          <Card title="Current plan">
            {current ? (
              <div className={pageStyles.inline}>
                <StatusBadge tone="info" label={current.plan} />
                <span className={pageStyles.muted}>Status: {current.status}</span>
              </div>
            ) : (
              <EmptyState title="No subscription" description="You are not subscribed to any plan." />
            )}
          </Card>

          <Card title="Available plans">
            {plans.length === 0 ? (
              <EmptyState title="No plans" description="Plan catalog is not available." />
            ) : (
              <div className={pageStyles.tableWrap}>
                <Table>
                  <TableHead>
                    <TableRow>
                      <TableHeaderCell>Plan</TableHeaderCell>
                      <TableHeaderCell>Price</TableHeaderCell>
                      <TableHeaderCell>Currency</TableHeaderCell>
                      <TableHeaderCell>Billing</TableHeaderCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {plans.map((plan) => (
                      <TableRow key={plan.plan_id}>
                        <TableCell>{plan.plan}</TableCell>
                        <TableCell>{plan.monthly_price}</TableCell>
                        <TableCell>{plan.currency}</TableCell>
                        <TableCell>Monthly</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </Card>
        </div>
      )}
    </section>
  )
}