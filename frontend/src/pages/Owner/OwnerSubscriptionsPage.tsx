import { FormEvent, useCallback, useEffect, useMemo, useState } from 'react'
import { SubscriptionStoreRow } from '../../api/admin'
import { getHttpErrorInfo } from '../../api/client'
import Button from '../../components/ui/Button'
import Card from '../../components/ui/Card'
import EmptyState from '../../components/ui/EmptyState'
import ErrorState from '../../components/ui/ErrorState'
import Loader from '../../components/ui/Loader'
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from '../../components/ui/Table'
import { useToast } from '../../components/ui/Toast'
import { useAdmin } from '../../hooks/useAdmin'
import formStyles from '../../styles/forms.module.css'
import pageStyles from '../../styles/page.module.css'

type PlanModalState = {
  companyId: number
  companyName: string
}

type ExtendModalState = {
  companyId: number
  companyName: string
}

export default function OwnerSubscriptionsPage() {
  const { getSubscriptionStores, setSubscriptionPlan, extendSubscription } = useAdmin()
  const { push } = useToast()
  const [rows, setRows] = useState<SubscriptionStoreRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [planModal, setPlanModal] = useState<PlanModalState | null>(null)
  const [extendModal, setExtendModal] = useState<ExtendModalState | null>(null)
  const [plan, setPlan] = useState('start')
  const [planReason, setPlanReason] = useState('')
  const [extendDays, setExtendDays] = useState(7)
  const [extendReason, setExtendReason] = useState('')
  const [actionError, setActionError] = useState<string | null>(null)
  const [actionLoading, setActionLoading] = useState(false)

  const sortedRows = useMemo(() => {
    return [...rows].sort((a, b) => a.company_name.localeCompare(b.company_name))
  }, [rows])

  const loadSubscriptions = useCallback(() => {
    setLoading(true)
    setError(null)
    getSubscriptionStores()
      .then((data) => {
        setRows(data)
      })
      .catch((err) => {
        const info = getHttpErrorInfo(err)
        const statusPart = info.status ? ` (status ${info.status})` : ''
        setError(`Failed to load subscriptions${statusPart}: ${info.message}`)
      })
      .finally(() => setLoading(false))
  }, [getSubscriptionStores])

  useEffect(() => {
    loadSubscriptions()
  }, [loadSubscriptions])

  async function submitPlanChange(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!planModal) return
    setActionLoading(true)
    setActionError(null)
    try {
      await setSubscriptionPlan(planModal.companyId, { plan, reason: planReason || 'plan change' })
      setPlanModal(null)
      setPlan('start')
      setPlanReason('')
      push('Plan updated.', 'success')
      loadSubscriptions()
    } catch (err) {
      const info = getHttpErrorInfo(err)
      const statusPart = info.status ? ` (status ${info.status})` : ''
      setActionError(`Failed to set plan${statusPart}: ${info.message}`)
      push('Failed to update plan.', 'danger')
    } finally {
      setActionLoading(false)
    }
  }

  async function submitExtend(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!extendModal) return
    setActionLoading(true)
    setActionError(null)
    try {
      await extendSubscription(extendModal.companyId, {
        days: extendDays,
        reason: extendReason || 'extend',
      })
      setExtendModal(null)
      setExtendDays(7)
      setExtendReason('')
      push('Subscription extended.', 'success')
      loadSubscriptions()
    } catch (err) {
      const info = getHttpErrorInfo(err)
      const statusPart = info.status ? ` (status ${info.status})` : ''
      setActionError(`Failed to extend subscription${statusPart}: ${info.message}`)
      push('Failed to extend subscription.', 'danger')
    } finally {
      setActionLoading(false)
    }
  }

  return (
    <section className={pageStyles.page}>
      <div className={pageStyles.pageHeader}>
        <div>
          <h1 className={pageStyles.pageTitle}>Subscriptions</h1>
          <p className={pageStyles.pageDescription}>Manage plans and subscription periods per company.</p>
        </div>
      </div>

      <Card>
        {loading && <Loader label="Loading subscriptions..." />}
        {error && <ErrorState message={error} onRetry={loadSubscriptions} />}
        {!loading && !error && sortedRows.length === 0 && (
          <EmptyState title="No subscriptions" description="No subscription data is available yet." />
        )}

        {!loading && !error && sortedRows.length > 0 && (
          <div className={pageStyles.tableWrap}>
            <Table>
              <TableHead>
                <TableRow>
                  <TableHeaderCell>Company</TableHeaderCell>
                  <TableHeaderCell>Plan</TableHeaderCell>
                  <TableHeaderCell>Status</TableHeaderCell>
                  <TableHeaderCell>Period</TableHeaderCell>
                  <TableHeaderCell>Balance</TableHeaderCell>
                  <TableHeaderCell>Actions</TableHeaderCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {sortedRows.map((row) => (
                  <TableRow key={row.company_id}>
                    <TableCell>{row.company_name}</TableCell>
                    <TableCell>{row.plan}</TableCell>
                    <TableCell>{row.status}</TableCell>
                    <TableCell>
                      {row.current_period_start ?? '—'} → {row.current_period_end ?? '—'}
                    </TableCell>
                    <TableCell>{row.wallet_balance}</TableCell>
                    <TableCell>
                      <div className={pageStyles.inline}>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => setPlanModal({ companyId: row.company_id, companyName: row.company_name })}
                        >
                          Change plan
                        </Button>
                        <Button
                          size="sm"
                          onClick={() => setExtendModal({ companyId: row.company_id, companyName: row.company_name })}
                        >
                          Extend
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </Card>

      {planModal && (
        <div className={formStyles.modalOverlay}>
          <div className={formStyles.modal}>
            <h2>Change plan: {planModal.companyName}</h2>
            <form onSubmit={submitPlanChange} className={formStyles.formGrid}>
              <div className={formStyles.formRow}>
                <label className={formStyles.label}>Plan</label>
                <select className={formStyles.select} value={plan} onChange={(e) => setPlan(e.target.value)}>
                  <option value="start">Start</option>
                  <option value="pro">Pro</option>
                  <option value="business">Business</option>
                </select>
              </div>
              <div className={formStyles.formRow}>
                <label className={formStyles.label}>Reason</label>
                <input
                  className={formStyles.input}
                  value={planReason}
                  onChange={(e) => setPlanReason(e.target.value)}
                  placeholder="Manual activation"
                />
              </div>
              {actionError && <ErrorState message={actionError} />}
              <div className={formStyles.modalActions}>
                <Button type="button" variant="ghost" onClick={() => setPlanModal(null)}>
                  Cancel
                </Button>
                <Button type="submit" disabled={actionLoading}>
                  {actionLoading ? 'Saving...' : 'Save'}
                </Button>
              </div>
            </form>
          </div>
        </div>
      )}

      {extendModal && (
        <div className={formStyles.modalOverlay}>
          <div className={formStyles.modal}>
            <h2>Extend: {extendModal.companyName}</h2>
            <form onSubmit={submitExtend} className={formStyles.formGrid}>
              <div className={formStyles.formRow}>
                <label className={formStyles.label}>Days</label>
                <input
                  className={formStyles.input}
                  type="number"
                  value={extendDays}
                  onChange={(e) => setExtendDays(Number(e.target.value || 0))}
                  min={1}
                  max={365}
                />
              </div>
              <div className={formStyles.formRow}>
                <label className={formStyles.label}>Reason</label>
                <input
                  className={formStyles.input}
                  value={extendReason}
                  onChange={(e) => setExtendReason(e.target.value)}
                  placeholder="Extension by request"
                />
              </div>
              {actionError && <ErrorState message={actionError} />}
              <div className={formStyles.modalActions}>
                <Button type="button" variant="ghost" onClick={() => setExtendModal(null)}>
                  Cancel
                </Button>
                <Button type="submit" disabled={actionLoading}>
                  {actionLoading ? 'Saving...' : 'Extend'}
                </Button>
              </div>
            </form>
          </div>
        </div>
      )}
    </section>
  )
}