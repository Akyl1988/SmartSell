import { useEffect, useState } from 'react'
import { DashboardStats, getDashboardStats } from '../../api/analytics'
import { getHttpErrorInfo } from '../../api/client'
import Card from '../../components/ui/Card'
import EmptyState from '../../components/ui/EmptyState'
import ErrorState from '../../components/ui/ErrorState'
import Loader from '../../components/ui/Loader'
import pageStyles from '../../styles/page.module.css'

export default function DashboardPage() {
  const [stats, setStats] = useState<DashboardStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setLoading(true)
    setError(null)
    getDashboardStats()
      .then((data) => setStats(data))
      .catch((err) => {
        const info = getHttpErrorInfo(err)
        const statusPart = info.status ? ` (status ${info.status})` : ''
        setError(`Failed to load dashboard stats${statusPart}: ${info.message}`)
      })
      .finally(() => setLoading(false))
  }, [])

  return (
    <section className={pageStyles.page}>
      <div className={pageStyles.pageHeader}>
        <div>
          <h1 className={pageStyles.pageTitle}>Dashboard</h1>
          <p className={pageStyles.pageDescription}>Key store performance metrics at a glance.</p>
        </div>
      </div>

      {loading && <Loader label="Loading dashboard..." />}
      {error && <ErrorState message={error} />}
      {!loading && !error && !stats && (
        <EmptyState title="No data" description="Dashboard metrics are not available yet." />
      )}

      {!loading && !error && stats && (
        <div className={pageStyles.cardGrid}>
          <Card title="Total orders">
            <div>{stats.total_orders}</div>
          </Card>
          <Card title="Total revenue">
            <div>{stats.total_revenue}</div>
          </Card>
          <Card title="Total products">
            <div>{stats.total_products}</div>
          </Card>
          <Card title="Total customers">
            <div>{stats.total_customers}</div>
          </Card>
          <Card title="Low stock alerts">
            <div>{stats.low_stock_alerts}</div>
          </Card>
        </div>
      )}
    </section>
  )
}