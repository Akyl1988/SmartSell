import { useEffect, useState } from 'react'
import { getHttpErrorInfo } from '../../api/client'
import { getPlatformSummary, PlatformSummary } from '../../api/admin'

const pageStyle: React.CSSProperties = {
  background: '#f3f4f6',
  minHeight: '100%',
  padding: '24px',
}

const headerStyle: React.CSSProperties = {
  background: 'linear-gradient(135deg, #1d4ed8, #4f46e5)',
  color: '#ffffff',
  borderRadius: 12,
  padding: '20px 24px',
  marginBottom: 20,
  boxShadow: '0 10px 30px rgba(15, 23, 42, 0.15)',
}

const gridStyle: React.CSSProperties = {
  display: 'grid',
  gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
  gap: 16,
}

const cardStyle: React.CSSProperties = {
  background: '#ffffff',
  borderRadius: 8,
  padding: 16,
  boxShadow: '0 1px 2px rgba(15, 23, 42, 0.08)',
}

const labelStyle: React.CSSProperties = {
  fontSize: 12,
  textTransform: 'uppercase',
  letterSpacing: '0.04em',
  color: '#64748b',
  marginBottom: 8,
}

const valueStyle: React.CSSProperties = {
  fontSize: 22,
  fontWeight: 700,
  color: '#0f172a',
}

const statusRowStyle: React.CSSProperties = {
  display: 'flex',
  gap: 12,
  flexWrap: 'wrap',
  marginTop: 12,
}

const statusPillStyle = (ok: boolean): React.CSSProperties => ({
  display: 'inline-flex',
  alignItems: 'center',
  gap: 8,
  padding: '6px 10px',
  borderRadius: 999,
  background: ok ? 'rgba(37, 99, 235, 0.12)' : 'rgba(239, 68, 68, 0.12)',
  color: ok ? '#1d4ed8' : '#b91c1c',
  fontSize: 12,
  fontWeight: 600,
})

const statusDotStyle = (ok: boolean): React.CSSProperties => ({
  width: 8,
  height: 8,
  borderRadius: '50%',
  background: ok ? '#2563eb' : '#ef4444',
  boxShadow: ok ? '0 0 0 4px rgba(37, 99, 235, 0.12)' : '0 0 0 4px rgba(239, 68, 68, 0.12)',
})

export default function OwnerDashboardPage() {
  const [summary, setSummary] = useState<PlatformSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setLoading(true)
    getPlatformSummary()
      .then((data) => {
        setSummary(data)
        setError(null)
      })
      .catch((err) => {
        const info = getHttpErrorInfo(err)
        const statusPart = info.status ? ` (status ${info.status})` : ''
        setError(`Failed to load platform summary${statusPart}: ${info.message}`)
      })
      .finally(() => setLoading(false))
  }, [])

  return (
    <section style={pageStyle}>
      <div style={headerStyle}>
        <h1 style={{ margin: 0, fontSize: 24 }}>Обзор платформы</h1>
        <p style={{ margin: '6px 0 0', color: 'rgba(255,255,255,0.85)' }}>
          Ключевые метрики по компаниям, подпискам и инфраструктуре.
        </p>
      </div>

      {loading && <p>Загрузка...</p>}
      {error && <p style={{ color: '#b91c1c' }}>{error}</p>}

      {!loading && !error && summary && (
        <div style={gridStyle}>
          <div style={cardStyle}>
            <div style={labelStyle}>Компании</div>
            <div style={valueStyle}>{summary.companies_total}</div>
            <div style={{ color: '#64748b', marginTop: 4 }}>
              Активных: {summary.companies_active}
            </div>
          </div>
          <div style={cardStyle}>
            <div style={labelStyle}>Подписки</div>
            <div style={valueStyle}>{summary.subscriptions.total}</div>
            <div style={{ color: '#64748b', marginTop: 4 }}>
              Free: {summary.subscriptions.by_plan.free} · Trial: {summary.subscriptions.by_plan.trial} · Pro:{' '}
              {summary.subscriptions.by_plan.pro}
            </div>
          </div>
          <div style={cardStyle}>
            <div style={labelStyle}>Баланс кошельков</div>
            <div style={valueStyle}>{summary.wallet.total_balance}</div>
            <div style={{ color: '#64748b', marginTop: 4 }}>
              Активных кошельков: {summary.wallet.active_wallets}
            </div>
          </div>
          <div style={cardStyle}>
            <div style={labelStyle}>Kaspi связки</div>
            <div style={valueStyle}>{summary.stores_with_kaspi_connected}</div>
            <div style={{ color: '#64748b', marginTop: 4 }}>магазинов подключено</div>
          </div>
          <div style={cardStyle}>
            <div style={labelStyle}>Health</div>
            <div style={statusRowStyle}>
              <span style={statusPillStyle(summary.health.db_ok)}>
                <span style={statusDotStyle(summary.health.db_ok)} /> DB
              </span>
              <span style={statusPillStyle(summary.health.redis_ok)}>
                <span style={statusDotStyle(summary.health.redis_ok)} /> Redis
              </span>
              <span style={statusPillStyle(summary.health.worker_ok)}>
                <span style={statusDotStyle(summary.health.worker_ok)} /> Worker
              </span>
            </div>
          </div>
        </div>
      )}
    </section>
  )
}
