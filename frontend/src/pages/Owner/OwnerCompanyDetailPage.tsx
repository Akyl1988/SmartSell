import { FormEvent, useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import { AdminInviteResponse, CompanyDetail, KaspiTrialGrantOut } from '../../api/admin'
import { getHttpErrorInfo } from '../../api/client'
import Button from '../../components/ui/Button'
import Card from '../../components/ui/Card'
import EmptyState from '../../components/ui/EmptyState'
import ErrorState from '../../components/ui/ErrorState'
import Loader from '../../components/ui/Loader'
import StatusBadge from '../../components/ui/StatusBadge'
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from '../../components/ui/Table'
import { useToast } from '../../components/ui/Toast'
import { useAdmin } from '../../hooks/useAdmin'
import formStyles from '../../styles/forms.module.css'
import pageStyles from '../../styles/page.module.css'

function formatStatus(detail: CompanyDetail | null): { label: string; tone: 'success' | 'warning' } {
  if (!detail || !detail.plan_expires_at) {
    return { label: 'Active', tone: 'success' }
  }
  const expiresAt = new Date(detail.plan_expires_at)
  if (Number.isNaN(expiresAt.getTime()) || expiresAt > new Date()) {
    return { label: 'Active', tone: 'success' }
  }
  return { label: 'Expired', tone: 'warning' }
}

export default function OwnerCompanyDetailPage() {
  const { id } = useParams<{ id: string }>()
  const { getCompanyDetail, createAdminInvite, grantKaspiTrial } = useAdmin()
  const { push } = useToast()
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
  const [trialOpen, setTrialOpen] = useState(false)
  const [trialMerchantUid, setTrialMerchantUid] = useState('')
  const [trialDays, setTrialDays] = useState(15)
  const [trialPlan, setTrialPlan] = useState('pro')
  const [trialLoading, setTrialLoading] = useState(false)
  const [trialError, setTrialError] = useState<string | null>(null)
  const [trialResult, setTrialResult] = useState<KaspiTrialGrantOut | null>(null)

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
  }, [getCompanyDetail, id])

  const statusInfo = useMemo(() => formatStatus(company), [company])

  async function handleInviteSubmit(event: FormEvent<HTMLFormElement>) {
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
      push('Admin invite created.', 'success')
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(result.invite_url)
      }
    } catch (err) {
      const info = getHttpErrorInfo(err)
      const statusPart = info.status ? ` (status ${info.status})` : ''
      setInviteError(`Failed to create invite${statusPart}: ${info.message}`)
      push('Failed to create admin invite.', 'danger')
    } finally {
      setInviteLoading(false)
    }
  }

  async function handleTrialSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!company) return
    setTrialLoading(true)
    setTrialError(null)
    try {
      const result = await grantKaspiTrial({
        companyId: company.id,
        merchant_uid: trialMerchantUid,
        plan: trialPlan,
        trial_days: trialDays,
      })
      setTrialResult(result)
      push('Kaspi trial granted.', 'success')
    } catch (err) {
      const info = getHttpErrorInfo(err)
      const statusPart = info.status ? ` (status ${info.status})` : ''
      setTrialError(`Failed to grant Kaspi trial${statusPart}: ${info.message}`)
      push('Failed to grant Kaspi trial.', 'danger')
    } finally {
      setTrialLoading(false)
    }
  }

  return (
    <section className={pageStyles.page}>
      <div className={pageStyles.pageHeader}>
        <div>
          <h1 className={pageStyles.pageTitle}>{company?.name ?? 'Company detail'}</h1>
          <p className={pageStyles.pageDescription}>Review company status, admins, and plan settings.</p>
        </div>
        <div className={pageStyles.pageActions}>
          <Button
            type="button"
            variant="ghost"
            onClick={() => {
              setTrialOpen(true)
              setTrialMerchantUid(company?.kaspi_store_id ?? '')
              setTrialDays(15)
              setTrialPlan('pro')
              setTrialError(null)
              setTrialResult(null)
            }}
            disabled={!company}
          >
            Kaspi trial
          </Button>
          <Button type="button" onClick={() => setInviteOpen(true)} disabled={!company}>
            Create admin invite
          </Button>
        </div>
      </div>

      {loading && <Loader label="Loading company details..." />}
      {error && <ErrorState message={error} />}
      {!loading && !error && !company && (
        <EmptyState title="Company not found" description="We couldn't load this company." />
      )}

      {!loading && !error && company && (
        <div className={pageStyles.section}>
          <Card title="Company info">
            <div className={pageStyles.stack}>
              <div>Name: {company.name}</div>
              <div>BIN/IIN: {company.bin_iin ?? '—'}</div>
              <div>Kaspi Store ID: {company.kaspi_store_id ?? '—'}</div>
              <div>
                Status: <StatusBadge tone={statusInfo.tone} label={statusInfo.label} />
              </div>
            </div>
          </Card>

          <Card title="Subscription">
            <div className={pageStyles.stack}>
              <div>Plan: {company.current_plan ?? '—'}</div>
              <div>Period end: {company.plan_expires_at ?? '—'}</div>
            </div>
          </Card>

          <Card title="Administrators">
            {company.admins.length === 0 ? (
              <EmptyState title="No admins" description="This company has no administrators yet." />
            ) : (
              <div className={pageStyles.tableWrap}>
                <Table>
                  <TableHead>
                    <TableRow>
                      <TableHeaderCell>Phone</TableHeaderCell>
                      <TableHeaderCell>Role</TableHeaderCell>
                      <TableHeaderCell>Status</TableHeaderCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {company.admins.map((admin, index) => (
                      <TableRow key={`${admin.phone ?? 'admin'}-${index}`}>
                        <TableCell>{admin.phone ?? '—'}</TableCell>
                        <TableCell>{admin.role}</TableCell>
                        <TableCell>
                          <StatusBadge
                            tone={admin.is_active ? 'success' : 'danger'}
                            label={admin.is_active ? 'Active' : 'Inactive'}
                          />
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </Card>
        </div>
      )}

      {inviteOpen && (
        <div className={formStyles.modalOverlay}>
          <div className={formStyles.modal}>
            <h2>Admin invite</h2>
            <form onSubmit={handleInviteSubmit} className={formStyles.formGrid}>
              <div className={formStyles.formRow}>
                <label className={formStyles.label}>Phone</label>
                <input
                  className={formStyles.input}
                  value={invitePhone}
                  onChange={(event) => setInvitePhone(event.target.value)}
                  placeholder="77001234567"
                />
              </div>
              <div className={formStyles.formRow}>
                <label className={formStyles.label}>Grace days</label>
                <input
                  className={formStyles.input}
                  type="number"
                  value={graceDays}
                  onChange={(event) => setGraceDays(Number(event.target.value || 0))}
                  min={1}
                  max={60}
                />
              </div>
              <div className={formStyles.formRow}>
                <label className={formStyles.label}>Initial plan</label>
                <select
                  className={formStyles.select}
                  value={initialPlan}
                  onChange={(event) => setInitialPlan(event.target.value as 'trial_pro' | 'free' | 'pro')}
                >
                  <option value="trial_pro">Trial Pro</option>
                  <option value="free">Free</option>
                  <option value="pro">Pro</option>
                </select>
              </div>
              {inviteError && <ErrorState message={inviteError} />}
              {inviteResult && (
                <StatusBadge tone="success" label={`Invite URL copied to clipboard.`} />
              )}
              <div className={formStyles.modalActions}>
                <Button type="button" variant="ghost" onClick={() => setInviteOpen(false)}>
                  Close
                </Button>
                <Button type="submit" disabled={inviteLoading}>
                  {inviteLoading ? 'Creating...' : 'Create'}
                </Button>
              </div>
            </form>
          </div>
        </div>
      )}

      {trialOpen && company && (
        <div className={formStyles.modalOverlay}>
          <div className={formStyles.modal}>
            <h2>Kaspi trial</h2>
            <form onSubmit={handleTrialSubmit} className={formStyles.formGrid}>
              <div className={formStyles.formRow}>
                <label className={formStyles.label}>Merchant UID</label>
                <input
                  className={formStyles.input}
                  value={trialMerchantUid}
                  onChange={(event) => setTrialMerchantUid(event.target.value)}
                  placeholder="kaspi-merchant-uid"
                  required
                />
              </div>
              <div className={formStyles.formRow}>
                <label className={formStyles.label}>Plan</label>
                <select
                  className={formStyles.select}
                  value={trialPlan}
                  onChange={(event) => setTrialPlan(event.target.value)}
                >
                  <option value="pro">Pro</option>
                  <option value="start">Start</option>
                </select>
              </div>
              <div className={formStyles.formRow}>
                <label className={formStyles.label}>Trial days</label>
                <input
                  className={formStyles.input}
                  type="number"
                  value={trialDays}
                  onChange={(event) => setTrialDays(Number(event.target.value || 1))}
                  min={1}
                  max={15}
                />
              </div>
              {trialError && <ErrorState message={trialError} />}
              {trialResult && (
                <StatusBadge
                  tone="success"
                  label={`Granted until ${trialResult.active_until ?? '—'} (subscription #${trialResult.subscription_id})`}
                />
              )}
              <div className={formStyles.modalActions}>
                <Button type="button" variant="ghost" onClick={() => setTrialOpen(false)}>
                  Close
                </Button>
                <Button type="submit" disabled={trialLoading}>
                  {trialLoading ? 'Granting...' : 'Grant'}
                </Button>
              </div>
            </form>
          </div>
        </div>
      )}
    </section>
  )
}