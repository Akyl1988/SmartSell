import { FormEvent, useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { CompanyListItem, KaspiTrialGrantOut } from '../../api/admin'
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

type TrialModalState = {
  company: CompanyListItem
}

export default function OwnerCompaniesPage() {
  const { getCompanies, grantKaspiTrial } = useAdmin()
  const { push } = useToast()
  const navigate = useNavigate()
  const [items, setItems] = useState<CompanyListItem[]>([])
  const [page, setPage] = useState(1)
  const [size] = useState(20)
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [trialModal, setTrialModal] = useState<TrialModalState | null>(null)
  const [merchantUid, setMerchantUid] = useState('')
  const [trialDays, setTrialDays] = useState(15)
  const [trialPlan, setTrialPlan] = useState('pro')
  const [trialLoading, setTrialLoading] = useState(false)
  const [trialError, setTrialError] = useState<string | null>(null)
  const [trialResult, setTrialResult] = useState<KaspiTrialGrantOut | null>(null)

  const loadCompanies = useCallback(
    async (nextPage: number, nextQuery: string) => {
      setLoading(true)
      setError(null)
      try {
        const data = await getCompanies({ page: nextPage, size, q: nextQuery })
        setItems(data.items)
      } catch (err) {
        const info = getHttpErrorInfo(err)
        const statusPart = info.status ? ` (status ${info.status})` : ''
        setError(`Failed to load companies${statusPart}: ${info.message}`)
      } finally {
        setLoading(false)
      }
    },
    [getCompanies, size]
  )

  useEffect(() => {
    loadCompanies(page, query)
  }, [loadCompanies, page, query])

  function onSearchSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setPage(1)
    loadCompanies(1, query)
  }

  function openTrialModal(company: CompanyListItem) {
    setTrialModal({ company })
    setMerchantUid(company.kaspi_store_id ?? '')
    setTrialDays(15)
    setTrialPlan('pro')
    setTrialError(null)
    setTrialResult(null)
  }

  async function submitKaspiTrial(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!trialModal) return
    setTrialLoading(true)
    setTrialError(null)
    try {
      const result = await grantKaspiTrial({
        companyId: trialModal.company.id,
        merchant_uid: merchantUid,
        plan: trialPlan,
        trial_days: trialDays,
      })
      setTrialResult(result)
      push('Kaspi trial granted.', 'success')
      loadCompanies(page, query)
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
          <h1 className={pageStyles.pageTitle}>Companies</h1>
          <p className={pageStyles.pageDescription}>Browse companies, plans, and Kaspi availability.</p>
        </div>
      </div>

      <Card>
        <form onSubmit={onSearchSubmit} className={pageStyles.toolbar}>
          <div className={[formStyles.formRow, pageStyles.toolbarGrow].join(' ')}>
            <label className={formStyles.label}>Search</label>
            <input
              className={formStyles.input}
              type="text"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search by name or BIN/IIN"
            />
          </div>
          <Button type="submit" variant="primary">
            Search
          </Button>
          <div className={[pageStyles.inline, pageStyles.toolbarSpacer].join(' ')}>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => setPage((prev) => Math.max(1, prev - 1))}
              disabled={page <= 1 || loading}
            >
              Prev
            </Button>
            <span className={pageStyles.muted}>Page {page}</span>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => setPage((prev) => prev + 1)}
              disabled={loading}
            >
              Next
            </Button>
          </div>
        </form>

        {loading && <Loader label="Loading companies..." />}
        {error && <ErrorState message={error} onRetry={() => loadCompanies(page, query)} />}

        {!loading && !error && items.length === 0 && (
          <EmptyState title="No companies" description="No companies matched your search." />
        )}

        {!loading && !error && items.length > 0 && (
          <div className={pageStyles.tableWrap}>
            <Table>
              <TableHead>
                <TableRow>
                  <TableHeaderCell>ID</TableHeaderCell>
                  <TableHeaderCell>Name</TableHeaderCell>
                  <TableHeaderCell>BIN/IIN</TableHeaderCell>
                  <TableHeaderCell>Kaspi Store ID</TableHeaderCell>
                  <TableHeaderCell>Plan</TableHeaderCell>
                  <TableHeaderCell>Status</TableHeaderCell>
                  <TableHeaderCell>Plan ends</TableHeaderCell>
                  <TableHeaderCell>Actions</TableHeaderCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {items.map((company) => (
                  <TableRow key={company.id}>
                    <TableCell>#{company.id}</TableCell>
                    <TableCell>{company.name}</TableCell>
                    <TableCell>{company.bin_iin ?? '—'}</TableCell>
                    <TableCell>{company.kaspi_store_id ?? '—'}</TableCell>
                    <TableCell>{company.current_plan ?? '—'}</TableCell>
                    <TableCell>
                      <StatusBadge
                        tone={company.is_active ? 'success' : 'danger'}
                        label={company.is_active ? 'Active' : 'Inactive'}
                      />
                    </TableCell>
                    <TableCell>{company.plan_expires_at ?? '—'}</TableCell>
                    <TableCell>
                      <div className={pageStyles.inline}>
                        <Button size="sm" variant="ghost" onClick={() => openTrialModal(company)}>
                          Grant trial
                        </Button>
                        <Button size="sm" variant="primary" onClick={() => navigate(`/owner/companies/${company.id}`)}>
                          Open
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

      {trialModal && (
        <div className={formStyles.modalOverlay}>
          <div className={formStyles.modal}>
            <h2>Kaspi trial: {trialModal.company.name}</h2>
            <form onSubmit={submitKaspiTrial} className={formStyles.formGrid}>
              <div className={formStyles.formRow}>
                <label className={formStyles.label}>Merchant UID</label>
                <input
                  className={formStyles.input}
                  value={merchantUid}
                  onChange={(event) => setMerchantUid(event.target.value)}
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
                  label={`Granted until ${trialResult.active_until ?? '—'}`}
                />
              )}
              <div className={formStyles.modalActions}>
                <Button type="button" variant="ghost" onClick={() => setTrialModal(null)}>
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