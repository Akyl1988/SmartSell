import { useCallback, useEffect, useMemo, useState } from 'react'
import { CompanyListItem, PlatformSummary } from '../../api/admin'
import { getHttpErrorInfo } from '../../api/client'
import Button from '../../components/ui/Button'
import Card from '../../components/ui/Card'
import EmptyState from '../../components/ui/EmptyState'
import ErrorState from '../../components/ui/ErrorState'
import Loader from '../../components/ui/Loader'
import StatusBadge from '../../components/ui/StatusBadge'
import { useAdmin } from '../../hooks/useAdmin'
import pageStyles from '../../styles/page.module.css'

export default function OwnerDashboardPage() {
  const { getPlatformSummary, getCompanies } = useAdmin()
  const [summary, setSummary] = useState<PlatformSummary | null>(null)
  const [recentKaspiTrials, setRecentKaspiTrials] = useState<CompanyListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const loadDashboard = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [platform, companies] = await Promise.all([getPlatformSummary(), getCompanies({ page: 1, size: 20 })])
      setSummary(platform)
      const trials = companies.items.filter((company) => {
        const plan = (company.current_plan ?? '').toLowerCase()
        return company.kaspi_store_id && plan.includes('trial')
      })
      setRecentKaspiTrials(trials.slice(0, 5))
    } catch (err) {
      const info = getHttpErrorInfo(err)
      const statusPart = info.status ? ` (status ${info.status})` : ''
      setError(`Failed to load platform summary${statusPart}: ${info.message}`)
    } finally {
      setLoading(false)
    }
  }, [getCompanies, getPlatformSummary])

  useEffect(() => {
    loadDashboard()
  }, [loadDashboard])

  const healthBadges = useMemo(() => {
    if (!summary) return []
    return [
      { label: 'DB', ok: summary.health.db_ok },
      { label: 'Redis', ok: summary.health.redis_ok },
      { label: 'Worker', ok: summary.health.worker_ok },
    ]
  }, [summary])

  return (
    <section className={pageStyles.page}>
      <div className={pageStyles.pageHeader}>
        <div>
          <h1 className={pageStyles.pageTitle}>Platform overview</h1>
          <p className={pageStyles.pageDescription}>Key metrics across companies, subscriptions, and infrastructure.</p>
        </div>
        <div className={pageStyles.pageActions}>
          <Button variant="ghost" onClick={loadDashboard} disabled={loading}>
            Refresh
          </Button>
        </div>
      </div>

      {loading && <Loader label="Loading platform summary..." />}
      {error && <ErrorState message={error} onRetry={loadDashboard} />}

      {!loading && !error && !summary && (
        <EmptyState title="No data" description="Platform summary is not available yet." />
      )}

      {!loading && !error && summary && (
        <div className={pageStyles.section}>
          <div className={pageStyles.cardGrid}>
            <Card title="Companies">
              <div className={pageStyles.stack}>
                <div className={pageStyles.muted}>Total companies</div>
                <div>{summary.companies_total}</div>
                <div className={pageStyles.muted}>Active: {summary.companies_active}</div>
              </div>
            </Card>
            <Card title="Subscriptions">
              <div className={pageStyles.stack}>
                <div className={pageStyles.muted}>Total</div>
                <div>{summary.subscriptions.total}</div>
                <div className={pageStyles.muted}>
                  Free: {summary.subscriptions.by_plan.free} · Trial: {summary.subscriptions.by_plan.trial} · Pro:{' '}
                  {summary.subscriptions.by_plan.pro}
                </div>
              </div>
            </Card>
            <Card title="Wallet balance">
              <div className={pageStyles.stack}>
                <div className={pageStyles.muted}>Total balance</div>
                <div>{summary.wallet.total_balance}</div>
                <div className={pageStyles.muted}>Active wallets: {summary.wallet.active_wallets}</div>
              </div>
            </Card>
            <Card title="Kaspi connections">
              <div className={pageStyles.stack}>
                <div className={pageStyles.muted}>Connected stores</div>
                <div>{summary.stores_with_kaspi_connected}</div>
              </div>
            </Card>
            <Card title="Health">
              <div className={pageStyles.inline}>
                {healthBadges.map((badge) => (
                  <StatusBadge
                    key={badge.label}
                    tone={badge.ok ? 'success' : 'danger'}
                    label={badge.label}
                  />
                ))}
              </div>
            </Card>
          </div>

          <div className={pageStyles.gridTwo}>
            <Card title="Recent Kaspi trials" description="Latest trial activations detected in active companies.">
              {recentKaspiTrials.length === 0 ? (
                <EmptyState title="No trials" description="No recent Kaspi trials found in the last companies list." />
              ) : (
                <div className={pageStyles.stack}>
                  {recentKaspiTrials.map((company) => (
                    <div key={company.id}>
                      <div>{company.name}</div>
                      <div className={pageStyles.muted}>Kaspi ID: {company.kaspi_store_id ?? '—'}</div>
                    </div>
                  ))}
                </div>
              )}
            </Card>
            <Card title="Repricing activity" description="Recent platform-wide repricing runs.">
              <EmptyState
                title="No repricing data"
                description="Repricing run history is not exposed by the admin API yet."
              />
            </Card>
          </div>
        </div>
      )}
    </section>
  )
}