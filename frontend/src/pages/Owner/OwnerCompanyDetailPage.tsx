import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { AdminInviteResponse, CompanyDetail, createAdminInvite, getCompanyDetail } from '../../api/admin'
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

const sectionTitleStyle: React.CSSProperties = {
  marginBottom: 8,
  fontSize: 16,
  fontWeight: 600,
}

const buttonPrimaryStyle: React.CSSProperties = {
  background: '#4f46e5',
  color: '#ffffff',
  border: 'none',
  borderRadius: 6,
  padding: '8px 12px',
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
  width: 'min(480px, 92vw)',
  boxShadow: '0 20px 60px rgba(15, 23, 42, 0.2)',
}

const labelStyle: React.CSSProperties = {
  fontSize: 12,
  color: '#64748b',
  marginBottom: 6,
}

const inputStyle: React.CSSProperties = {
  width: '100%',
  padding: '8px 10px',
  borderRadius: 6,
  border: '1px solid #cbd5f5',
  marginBottom: 12,
}

function formatStatus(detail: CompanyDetail | null): string {
  if (!detail) return '—'
  if (!detail.plan_expires_at) return 'Active'
  const expiresAt = new Date(detail.plan_expires_at)
  if (Number.isNaN(expiresAt.getTime())) return 'Active'
  return expiresAt > new Date() ? 'Active' : 'Expired'
}

export default function OwnerCompanyDetailPage() {
  const { id } = useParams<{ id: string }>()
  const [company, setCompany] = useState<CompanyDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [inviteOpen, setInviteOpen] = useState(false)
  const [invitePhone, setInvitePhone] = useState('')
  const [graceDays, setGraceDays] = useState(7)
  const [initialPlan, setInitialPlan] = useState<'trial_pro' | 'free' | 'pro'>('trial_pro')
  const [inviteResult, setInviteResult] = useState<AdminInviteResponse | null>(null)
  const [inviteError, setInviteError] = useState<string | null>(null)
  const [inviteLoading, setInviteLoading] = useState(false)

  useEffect(() => {
    if (!id) return
    setLoading(true)
    getCompanyDetail(Number(id))
      .then((data) => {
        setCompany(data)
        setError(null)
      })
      .catch((err) => {
        const info = getHttpErrorInfo(err)
        const statusPart = info.status ? ` (status ${info.status})` : ''
        setError(`Failed to load company${statusPart}: ${info.message}`)
      })
      .finally(() => setLoading(false))
  }, [id])

  async function handleInviteSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!company) return
    setInviteLoading(true)
    setInviteError(null)
    try {
      const result = await createAdminInvite({
        company_id: company.id,
        phone: invitePhone,
        grace_days: graceDays,
        initial_plan: initialPlan,
      })
      setInviteResult(result)
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(result.invite_url)
      }
    } catch (err) {
      const info = getHttpErrorInfo(err)
      const statusPart = info.status ? ` (status ${info.status})` : ''
      setInviteError(`Failed to create invite${statusPart}: ${info.message}`)
    } finally {
      setInviteLoading(false)
    }
  }

  return (
    <section style={pageStyle}>
      {loading && <p>Загрузка...</p>}
      {error && <p style={{ color: '#b91c1c' }}>{error}</p>}

      {!loading && !error && company && (
        <>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <h1 style={{ marginBottom: 12 }}>{company.name}</h1>
            <button type="button" style={buttonPrimaryStyle} onClick={() => setInviteOpen(true)}>
              Создать инвайт администратора
            </button>
          </div>

          <div style={panelStyle}>
            <div style={sectionTitleStyle}>Основная информация</div>
            <div>Название: {company.name}</div>
            <div>BIN/IIN: {company.bin_iin ?? '—'}</div>
            <div>Kaspi Store ID: {company.kaspi_store_id ?? '—'}</div>
          </div>

          <div style={panelStyle}>
            <div style={sectionTitleStyle}>Подписка</div>
            <div>План: {company.current_plan ?? '—'}</div>
            <div>Период до: {company.plan_expires_at ?? '—'}</div>
            <div>Статус: {formatStatus(company)}</div>
          </div>

          <div style={panelStyle}>
            <div style={sectionTitleStyle}>Администраторы</div>
            <div style={{ display: 'grid', gap: 8 }}>
              {company.admins.length === 0 && <span>Нет администраторов</span>}
              {company.admins.map((admin, index) => (
                <div key={`${admin.phone ?? 'admin'}-${index}`}>
                  {admin.phone ?? '—'} · {admin.role} · {admin.is_active ? 'active' : 'inactive'}
                </div>
              ))}
            </div>
          </div>
        </>
      )}

      {inviteOpen && (
        <div style={modalOverlayStyle}>
          <div style={modalStyle}>
            <h2 style={{ marginTop: 0 }}>Инвайт администратора</h2>
            <form onSubmit={handleInviteSubmit}>
              <label style={labelStyle}>Телефон</label>
              <input
                style={inputStyle}
                value={invitePhone}
                onChange={(event) => setInvitePhone(event.target.value)}
                placeholder="77001234567"
              />
              <label style={labelStyle}>Grace days</label>
              <input
                type="number"
                style={inputStyle}
                value={graceDays}
                onChange={(event) => setGraceDays(Number(event.target.value || 0))}
                min={1}
                max={60}
              />
              <label style={labelStyle}>Initial plan</label>
              <select
                style={inputStyle}
                value={initialPlan}
                onChange={(event) => setInitialPlan(event.target.value as 'trial_pro' | 'free' | 'pro')}
              >
                <option value="trial_pro">Trial Pro</option>
                <option value="free">Free</option>
                <option value="pro">Pro</option>
              </select>

              {inviteError && <p style={{ color: '#b91c1c' }}>{inviteError}</p>}
              {inviteResult && (
                <div style={{ marginBottom: 12, color: '#1d4ed8' }}>
                  Invite URL: {inviteResult.invite_url}
                </div>
              )}

              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
                <button type="button" onClick={() => setInviteOpen(false)}>
                  Закрыть
                </button>
                <button type="submit" style={buttonPrimaryStyle} disabled={inviteLoading}>
                  {inviteLoading ? 'Отправка...' : 'Создать'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </section>
  )
}
