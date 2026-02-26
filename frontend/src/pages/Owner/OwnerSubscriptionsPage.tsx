import { useEffect, useMemo, useState } from 'react'
import {
  SubscriptionStoreRow,
  extendSubscription,
  getSubscriptionStores,
  setSubscriptionPlan,
} from '../../api/admin'
import { getHttpErrorInfo } from '../../api/client'

const pageStyle: React.CSSProperties = {
  background: '#f3f4f6',
  minHeight: '100%',
  padding: '24px',
}

const panelStyle: React.CSSProperties = {
  background: '#ffffff',
  borderRadius: 8,
  padding: 16,
  boxShadow: '0 1px 2px rgba(15, 23, 42, 0.08)',
}

const tableStyle: React.CSSProperties = {
  width: '100%',
  borderCollapse: 'collapse',
  marginTop: 12,
}

const thStyle: React.CSSProperties = {
  textAlign: 'left',
  fontSize: 12,
  color: '#64748b',
  padding: '10px 8px',
  borderBottom: '1px solid #e2e8f0',
}

const tdStyle: React.CSSProperties = {
  padding: '10px 8px',
  borderBottom: '1px solid #e2e8f0',
  fontSize: 14,
}

const actionButtonStyle: React.CSSProperties = {
  background: '#2563eb',
  color: '#ffffff',
  border: 'none',
  borderRadius: 6,
  padding: '6px 10px',
  marginRight: 8,
}

const modalOverlayStyle: React.CSSProperties = {
  position: 'fixed',
  inset: 0,
  background: 'rgba(15, 23, 42, 0.35)',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  zIndex: 30,
}

const modalStyle: React.CSSProperties = {
  background: '#ffffff',
  borderRadius: 10,
  padding: 20,
  width: 'min(460px, 92vw)',
  boxShadow: '0 20px 60px rgba(15, 23, 42, 0.2)',
}

const inputStyle: React.CSSProperties = {
  width: '100%',
  padding: '8px 10px',
  borderRadius: 6,
  border: '1px solid #cbd5f5',
  marginBottom: 12,
}

type PlanModalState = {
  companyId: number
  companyName: string
}

type ExtendModalState = {
  companyId: number
  companyName: string
}

export default function OwnerSubscriptionsPage() {
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

  function loadSubscriptions() {
    setLoading(true)
    getSubscriptionStores()
      .then((data) => {
        setRows(data)
        setError(null)
      })
      .catch((err) => {
        const info = getHttpErrorInfo(err)
        const statusPart = info.status ? ` (status ${info.status})` : ''
        setError(`Failed to load subscriptions${statusPart}: ${info.message}`)
      })
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    loadSubscriptions()
  }, [])

  async function submitPlanChange(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!planModal) return
    setActionLoading(true)
    setActionError(null)
    try {
      await setSubscriptionPlan(planModal.companyId, { plan, reason: planReason || 'plan change' })
      setPlanModal(null)
      setPlan('start')
      setPlanReason('')
      loadSubscriptions()
    } catch (err) {
      const info = getHttpErrorInfo(err)
      const statusPart = info.status ? ` (status ${info.status})` : ''
      setActionError(`Failed to set plan${statusPart}: ${info.message}`)
    } finally {
      setActionLoading(false)
    }
  }

  async function submitExtend(event: React.FormEvent<HTMLFormElement>) {
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
      loadSubscriptions()
    } catch (err) {
      const info = getHttpErrorInfo(err)
      const statusPart = info.status ? ` (status ${info.status})` : ''
      setActionError(`Failed to extend subscription${statusPart}: ${info.message}`)
    } finally {
      setActionLoading(false)
    }
  }

  return (
    <section style={pageStyle}>
      <div style={{ marginBottom: 16 }}>
        <h1 style={{ marginBottom: 6 }}>Подписки</h1>
        <p style={{ color: '#64748b' }}>Управление подписками по магазинам.</p>
      </div>

      <div style={panelStyle}>
        {loading && <p>Загрузка...</p>}
        {error && <p style={{ color: '#b91c1c' }}>{error}</p>}

        {!loading && !error && (
          <table style={tableStyle}>
            <thead>
              <tr>
                <th style={thStyle}>Магазин</th>
                <th style={thStyle}>План</th>
                <th style={thStyle}>Статус</th>
                <th style={thStyle}>Период</th>
                <th style={thStyle}>Баланс</th>
                <th style={thStyle}>Действия</th>
              </tr>
            </thead>
            <tbody>
              {sortedRows.map((row) => (
                <tr key={row.company_id}>
                  <td style={tdStyle}>{row.company_name}</td>
                  <td style={tdStyle}>{row.plan}</td>
                  <td style={tdStyle}>{row.status}</td>
                  <td style={tdStyle}>
                    {row.current_period_start ?? '—'} → {row.current_period_end ?? '—'}
                  </td>
                  <td style={tdStyle}>{row.wallet_balance}</td>
                  <td style={tdStyle}>
                    <button
                      type="button"
                      style={actionButtonStyle}
                      onClick={() => setPlanModal({ companyId: row.company_id, companyName: row.company_name })}
                    >
                      Сменить план
                    </button>
                    <button
                      type="button"
                      onClick={() => setExtendModal({ companyId: row.company_id, companyName: row.company_name })}
                    >
                      Продлить
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {planModal && (
        <div style={modalOverlayStyle}>
          <div style={modalStyle}>
            <h2 style={{ marginTop: 0 }}>Сменить план: {planModal.companyName}</h2>
            <form onSubmit={submitPlanChange}>
              <label style={{ fontSize: 12, color: '#64748b' }}>План</label>
              <select style={inputStyle} value={plan} onChange={(e) => setPlan(e.target.value)}>
                <option value="start">Start</option>
                <option value="pro">Pro</option>
                <option value="business">Business</option>
              </select>
              <label style={{ fontSize: 12, color: '#64748b' }}>Причина</label>
              <input
                style={inputStyle}
                value={planReason}
                onChange={(e) => setPlanReason(e.target.value)}
                placeholder="Например: ручная активация"
              />
              {actionError && <p style={{ color: '#b91c1c' }}>{actionError}</p>}
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
                <button type="button" onClick={() => setPlanModal(null)}>
                  Отмена
                </button>
                <button type="submit" style={actionButtonStyle} disabled={actionLoading}>
                  {actionLoading ? 'Сохранение...' : 'Сохранить'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {extendModal && (
        <div style={modalOverlayStyle}>
          <div style={modalStyle}>
            <h2 style={{ marginTop: 0 }}>Продлить: {extendModal.companyName}</h2>
            <form onSubmit={submitExtend}>
              <label style={{ fontSize: 12, color: '#64748b' }}>Дней</label>
              <input
                type="number"
                style={inputStyle}
                value={extendDays}
                onChange={(e) => setExtendDays(Number(e.target.value || 0))}
                min={1}
                max={365}
              />
              <label style={{ fontSize: 12, color: '#64748b' }}>Причина</label>
              <input
                style={inputStyle}
                value={extendReason}
                onChange={(e) => setExtendReason(e.target.value)}
                placeholder="Например: продление по запросу"
              />
              {actionError && <p style={{ color: '#b91c1c' }}>{actionError}</p>}
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
                <button type="button" onClick={() => setExtendModal(null)}>
                  Отмена
                </button>
                <button type="submit" style={actionButtonStyle} disabled={actionLoading}>
                  {actionLoading ? 'Сохранение...' : 'Продлить'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </section>
  )
}
