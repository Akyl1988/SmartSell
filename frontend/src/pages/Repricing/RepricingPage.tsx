import { useCallback, useEffect, useMemo, useState } from 'react'
import { getHttpErrorInfo } from '../../api/client'
import {
  applyRepricingRun,
  getRepricingRun,
  listRepricingRuns,
  RepricingRunResponse,
  runRepricing,
} from '../../api/pricing'
import { useFeatureGate } from '../../hooks/useFeatureGate'
import { useToast } from '../../components/ui/Toast'
import Button from '../../components/ui/Button'
import Card from '../../components/ui/Card'
import EmptyState from '../../components/ui/EmptyState'
import ErrorState from '../../components/ui/ErrorState'
import Loader from '../../components/ui/Loader'
import StatusBadge from '../../components/ui/StatusBadge'
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from '../../components/ui/Table'
import pageStyles from '../../styles/page.module.css'

const REFRESH_INTERVAL_MS = 4000

type DetailTab = 'updated' | 'skipped'
type RunItemRow = NonNullable<RepricingRunResponse['items']>[number]

export default function RepricingPage() {
  const { hasRepricing, paymentRequired } = useFeatureGate()
  const { push } = useToast()
  const [lastRun, setLastRun] = useState<RepricingRunResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [runBusy, setRunBusy] = useState(false)
  const [applyBusy, setApplyBusy] = useState(false)
  const [detailTab, setDetailTab] = useState<DetailTab>('updated')

  const loadLastRun = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const runs = await listRepricingRuns({ page: 1, per_page: 5 })
      if (runs.length === 0) {
        setLastRun(null)
        return
      }
      const latest = runs[0]
      const fullRun = await getRepricingRun(latest.id)
      setLastRun(fullRun)
    } catch (err) {
      const info = getHttpErrorInfo(err)
      const statusPart = info.status ? ` (status ${info.status})` : ''
      setError(`Failed to load repricing runs${statusPart}: ${info.message}`)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!hasRepricing || paymentRequired) return
    loadLastRun()
  }, [hasRepricing, paymentRequired, loadLastRun])

  const isRunning = useMemo(() => {
    const status = lastRun?.status ?? ''
    return status.toLowerCase().includes('running') || status.toLowerCase().includes('processing')
  }, [lastRun])

  useEffect(() => {
    if (!isRunning || !lastRun) return
    const timer = setInterval(async () => {
      try {
        const updated = await getRepricingRun(lastRun.id)
        setLastRun(updated)
      } catch {
        // silent refresh fail
      }
    }, REFRESH_INTERVAL_MS)
    return () => clearInterval(timer)
  }, [isRunning, lastRun])

  if (!hasRepricing || paymentRequired) {
    return (
      <section className={pageStyles.page}>
        <div className={pageStyles.pageHeader}>
          <div>
            <h1 className={pageStyles.pageTitle}>Repricing</h1>
            <p className={pageStyles.pageDescription}>Upgrade to Pro to use this feature.</p>
          </div>
        </div>
      </section>
    )
  }

  const runItems = (lastRun?.items ?? []) as RunItemRow[]
  const updatedRows = runItems.filter((item) => item.status === 'changed' || item.status === 'dry_run')
  const skippedRows = runItems.filter((item) => item.status === 'skipped')
  const updatedCount = updatedRows.length || lastRun?.changed || 0
  const skippedCount = skippedRows.length
  const statsRows = detailTab === 'updated' ? updatedRows : skippedRows

  async function onRun() {
    if (runBusy || isRunning) return
    setRunBusy(true)
    try {
      const response = await runRepricing(false)
      const fullRun = await getRepricingRun(response.run_id)
      setLastRun(fullRun)
    } catch (err) {
      const info = getHttpErrorInfo(err)
      const statusPart = info.status ? ` (status ${info.status})` : ''
      push(`Failed to run repricing${statusPart}: ${info.message}`, 'danger')
    } finally {
      setRunBusy(false)
    }
  }

  async function onApply() {
    if (!lastRun || applyBusy) return
    setApplyBusy(true)
    try {
      const response = await applyRepricingRun(lastRun.id, false)
      setLastRun(response)
    } catch (err) {
      const info = getHttpErrorInfo(err)
      const statusPart = info.status ? ` (status ${info.status})` : ''
      push(`Failed to apply repricing${statusPart}: ${info.message}`, 'danger')
    } finally {
      setApplyBusy(false)
    }
  }

  return (
    <section className={pageStyles.page}>
      <div className={pageStyles.pageHeader}>
        <div>
          <h1 className={pageStyles.pageTitle}>Repricing</h1>
          <p className={pageStyles.pageDescription}>Run automated repricing and review results.</p>
        </div>
        <div className={pageStyles.pageActions}>
          <Button variant="ghost" onClick={loadLastRun} disabled={loading}>
            Refresh
          </Button>
          <Button onClick={onRun} disabled={runBusy || isRunning || loading}>
            {runBusy || isRunning ? 'Running...' : 'Run repricing'}
          </Button>
          <Button variant="ghost" onClick={onApply} disabled={applyBusy || loading || !lastRun || isRunning}>
            {applyBusy ? 'Applying...' : 'Apply changes'}
          </Button>
        </div>
      </div>

      <Card>
        {loading && <Loader label="Loading repricing runs..." />}
        {error && <ErrorState message={error} onRetry={loadLastRun} />}
        {!loading && !error && !lastRun && (
          <EmptyState title="No repricing runs" description="Click “Run repricing” to create your first run." />
        )}

        {lastRun && (
          <div className={pageStyles.section}>
            <div className={pageStyles.cardGrid}>
              <Card title="Last run" description={`Run #${lastRun.id}`}>
                <div className={pageStyles.stack}>
                  <StatusBadge tone={isRunning ? 'warning' : 'info'} label={isRunning ? 'In progress' : lastRun.status} />
                  <div>
                    <div className={pageStyles.muted}>Started</div>
                    <div>{lastRun.started_at ? new Date(lastRun.started_at).toLocaleString() : '—'}</div>
                  </div>
                  <div>
                    <div className={pageStyles.muted}>Finished</div>
                    <div>{lastRun.finished_at ? new Date(lastRun.finished_at).toLocaleString() : '—'}</div>
                  </div>
                </div>
              </Card>
              <Card title="Updated">
                <div className={pageStyles.stack}>
                  <div className={pageStyles.muted}>Updated count</div>
                  <div>{updatedCount}</div>
                </div>
              </Card>
              <Card title="Skipped">
                <div className={pageStyles.stack}>
                  <div className={pageStyles.muted}>Skipped count</div>
                  <div>{skippedCount}</div>
                </div>
              </Card>
            </div>

            {lastRun.last_error && <ErrorState message={`Last error: ${lastRun.last_error}`} />}

            <div className={pageStyles.pillRow}>
              <Button
                variant={detailTab === 'updated' ? 'primary' : 'ghost'}
                size="sm"
                onClick={() => setDetailTab('updated')}
              >
                Updated ({updatedCount})
              </Button>
              <Button
                variant={detailTab === 'skipped' ? 'primary' : 'ghost'}
                size="sm"
                onClick={() => setDetailTab('skipped')}
              >
                Skipped ({skippedCount})
              </Button>
            </div>

            <div className={pageStyles.tableWrap}>
              <Table>
                <TableHead>
                  <TableRow>
                    <TableHeaderCell>Product ID</TableHeaderCell>
                    <TableHeaderCell>Old price</TableHeaderCell>
                    <TableHeaderCell>New price</TableHeaderCell>
                    <TableHeaderCell>Reason</TableHeaderCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {statsRows.length === 0 && (
                    <TableRow>
                        <TableCell colSpan={4}>
                        <EmptyState
                          title={`No ${detailTab} items`}
                          description="This run did not produce any entries for this view."
                        />
                      </TableCell>
                    </TableRow>
                  )}
                    {statsRows.map((row) => (
                      <TableRow key={`${detailTab}-${row.product_id ?? row.id}`}>
                        <TableCell>#{row.product_id ?? '—'}</TableCell>
                        <TableCell>{row.old_price ?? '—'}</TableCell>
                        <TableCell>{row.new_price ?? '—'}</TableCell>
                        <TableCell>{row.reason ?? '—'}</TableCell>
                      </TableRow>
                    ))}
                </TableBody>
              </Table>
            </div>
          </div>
        )}
      </Card>
    </section>
  )
}
