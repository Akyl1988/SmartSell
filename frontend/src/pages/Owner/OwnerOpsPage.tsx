import { useState } from 'react'
import { runSubscriptionRenew } from '../../api/admin'
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
  marginBottom: 16,
}

const actionButtonStyle: React.CSSProperties = {
  background: '#2563eb',
  color: '#ffffff',
  border: 'none',
  borderRadius: 6,
  padding: '8px 12px',
}

const inputStyle: React.CSSProperties = {
  padding: '8px 10px',
  borderRadius: 6,
  border: '1px solid #cbd5f5',
  width: 160,
}

export default function OwnerOpsPage() {
  const [renewStatus, setRenewStatus] = useState<string | null>(null)
  const [renewError, setRenewError] = useState<string | null>(null)
  const [renewLoading, setRenewLoading] = useState(false)

  const [preorderStatus, setPreorderStatus] = useState<string | null>(null)
  const [preorderError, setPreorderError] = useState<string | null>(null)

  const [kaspiCompanyId, setKaspiCompanyId] = useState('')
  const [kaspiStatus, setKaspiStatus] = useState<string | null>(null)
  const [kaspiError, setKaspiError] = useState<string | null>(null)

  async function handleRenew() {
    setRenewLoading(true)
    setRenewError(null)
    try {
      const result = await runSubscriptionRenew()
      setRenewStatus(`Processed: ${result.processed}`)
    } catch (err) {
      const info = getHttpErrorInfo(err)
      const statusPart = info.status ? ` (status ${info.status})` : ''
      setRenewError(`Failed to run renew${statusPart}: ${info.message}`)
    } finally {
      setRenewLoading(false)
    }
  }

  function handlePreorderCheck() {
    setPreorderStatus(null)
    setPreorderError('TODO: endpoint for E2E preorders is not wired yet.')
  }

  function handleKaspiSync() {
    setKaspiStatus(null)
    if (!kaspiCompanyId.trim()) {
      setKaspiError('Введите company_id для запуска синка.')
      return
    }
    setKaspiError('TODO: platform-admin sync endpoint not wired yet.')
  }

  return (
    <section style={pageStyle}>
      <div style={{ marginBottom: 16 }}>
        <h1 style={{ marginBottom: 6 }}>Операции</h1>
        <p style={{ color: '#64748b' }}>Ручные действия и диагностика.</p>
      </div>

      <div style={panelStyle}>
        <h3 style={{ marginTop: 0 }}>Подписки</h3>
        <button type="button" style={actionButtonStyle} onClick={handleRenew} disabled={renewLoading}>
          {renewLoading ? 'Запуск...' : 'Ручной запуск продления подписок'}
        </button>
        {renewStatus && <p style={{ color: '#1d4ed8' }}>{renewStatus}</p>}
        {renewError && <p style={{ color: '#b91c1c' }}>{renewError}</p>}
      </div>

      <div style={panelStyle}>
        <h3 style={{ marginTop: 0 }}>Проверки</h3>
        <button type="button" style={actionButtonStyle} onClick={handlePreorderCheck}>
          Проверка E2E предзаказов
        </button>
        {preorderStatus && <p style={{ color: '#1d4ed8' }}>{preorderStatus}</p>}
        {preorderError && <p style={{ color: '#b91c1c' }}>{preorderError}</p>}
      </div>

      <div style={panelStyle}>
        <h3 style={{ marginTop: 0 }}>Kaspi sync now</h3>
        <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
          <input
            style={inputStyle}
            placeholder="company_id"
            value={kaspiCompanyId}
            onChange={(event) => setKaspiCompanyId(event.target.value)}
          />
          <button type="button" style={actionButtonStyle} onClick={handleKaspiSync}>
            Запустить
          </button>
        </div>
        {kaspiStatus && <p style={{ color: '#1d4ed8' }}>{kaspiStatus}</p>}
        {kaspiError && <p style={{ color: '#b91c1c' }}>{kaspiError}</p>}
      </div>
    </section>
  )
}
