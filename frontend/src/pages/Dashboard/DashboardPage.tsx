import { useEffect, useState } from 'react'
import { DashboardStats, getDashboardStats } from '../../api/analytics'
import { getHttpErrorInfo } from '../../api/client'

export default function DashboardPage() {
  const [stats, setStats] = useState<DashboardStats | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getDashboardStats()
      .then(setStats)
      .catch((err) => {
        console.error('dashboard error', err)
        const info = getHttpErrorInfo(err)
        if (info.status) {
          setError(`Failed to load dashboard stats (status ${info.status}): ${info.message}`)
        } else {
          setError(`Failed to load dashboard stats: ${info.message}`)
        }
      })
  }, [])

  return (
    <section>
      <h1>Dashboard</h1>
      {error && <p style={{ color: '#b91c1c' }}>{error}</p>}
      {!error && !stats && <p>Loading...</p>}
      {stats && (
        <div style={{ display: 'grid', gap: 8 }}>
          <div>Total orders: {stats.total_orders}</div>
          <div>Total revenue: {stats.total_revenue}</div>
          <div>Total products: {stats.total_products}</div>
          <div>Total customers: {stats.total_customers}</div>
          <div>Low stock alerts: {stats.low_stock_alerts}</div>
        </div>
      )}
    </section>
  )
}
