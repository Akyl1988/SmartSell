import { useCallback, useEffect, useMemo, useState } from 'react'
import { getHttpErrorInfo } from '../../api/client'
import { listRepricingRuns, RepricingRunResponse, runRepricing } from '../../api/pricing'
import { useFeatureGate } from '../../hooks/useFeatureGate'

type StatusFilter = 'all' | 'running' | 'succeeded' | 'failed' | 'pending'

export default function RepricingPage() {
  const { hasRepricing, paymentRequired } = useFeatureGate()
  const [runs, setRuns] = useState<RepricingRunResponse[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [runBusy, setRunBusy] = useState(false)

  const loadRuns = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await listRepricingRuns({ page: 1, per_page: 50 })
      setRuns(data)
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
    loadRuns()
  }, [hasRepricing, paymentRequired, loadRuns])

  if (!hasRepricing || paymentRequired) {
    return (
      <section>
        <h1>Repricing</h1>
        <p>Upgrade to Pro to use this feature.</p>
      </section>
    )
  }

  const filteredRuns = useMemo(() => {
    if (statusFilter === 'all') return runs
    const normalized = statusFilter
    return runs.filter((run) => normalizeStatus(run.status) === normalized)
  }, [runs, statusFilter])

  function normalizeStatus(status: string) {
    const value = status.trim().toLowerCase()
    if (value.includes('running') || value.includes('processing')) return 'running'
    if (value.includes('fail') || value.includes('error')) return 'failed'
    if (value.includes('success') || value.includes('done') || value.includes('completed')) return 'succeeded'
    return 'pending'
  }

  function formatStatusLabel(status: string) {
    const normalized = normalizeStatus(status)
    if (normalized === 'running') return 'Running'
    if (normalized === 'failed') return 'Failed'
    if (normalized === 'succeeded') return 'Succeeded'
    return 'Pending'
  }

  function formatTimestampForFile(date: Date) {
    const year = date.getFullYear()
    const month = String(date.getMonth() + 1).padStart(2, '0')
    const day = String(date.getDate()).padStart(2, '0')
    const hour = String(date.getHours()).padStart(2, '0')
    const minute = String(date.getMinutes()).padStart(2, '0')
    return `${year}${month}${day}-${hour}${minute}`
  }

  function escapeCsvValue(value: string | number | null | undefined) {
    if (value === null || value === undefined) return ''
    const stringValue = String(value)
    if (/[",\n\r]/.test(stringValue)) {
      return `"${stringValue.replace(/"/g, '""')}"`
    }
    return stringValue
  }

  function exportRunsToCsv(data: RepricingRunResponse[]) {
    const header = ['id', 'status', 'created_at', 'started_at', 'finished_at', 'last_error']
    const rows = data.map((run) => [
      run.id,
      run.status,
      run.created_at,
      run.started_at ?? '',
      run.finished_at ?? '',
      run.last_error ?? '',
    ])

    const csvLines = [header, ...rows]
      .map((row) => row.map((value) => escapeCsvValue(value)).join(','))
      .join('\n')

    const blob = new Blob([csvLines], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const timestamp = formatTimestampForFile(new Date())
    const link = document.createElement('a')
    link.href = url
    link.download = `repricing-runs-${timestamp}.csv`
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    URL.revokeObjectURL(url)
  }

  async function onRun() {
    setRunBusy(true)
    setActionError(null)
    try {
      await runRepricing(false)
      await loadRuns()
    } catch (err) {
      const info = getHttpErrorInfo(err)
      const statusPart = info.status ? ` (status ${info.status})` : ''
      setActionError(`Failed to run repricing${statusPart}: ${info.message}`)
    } finally {
      setRunBusy(false)
    }
  }

  return (
    <section>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
        <h1>Repricing</h1>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value as StatusFilter)}>
            <option value="all">All statuses</option>
            <option value="pending">Pending</option>
            <option value="running">Running</option>
            <option value="succeeded">Succeeded</option>
            <option value="failed">Failed</option>
          </select>
          <button
            onClick={() => {
              try {
                setActionError(null)
                exportRunsToCsv(filteredRuns)
              } catch (err) {
                const info = getHttpErrorInfo(err)
                console.error('Failed to export repricing runs', err)
                setActionError(`Failed to export repricing runs: ${info.message}`)
              }
            }}
            disabled={loading || filteredRuns.length === 0 || error !== null}
          >
            Export CSV
          </button>
          <button
            onClick={() => {
              setActionError(null)
              loadRuns()
            }}
            disabled={loading}
          >
            Refresh
          </button>
          <button onClick={onRun} disabled={runBusy || loading}>
            {runBusy ? 'Running...' : 'Run repricing'}
          </button>
        </div>
      </div>
      {loading && <p>Loading repricing runs...</p>}
      {error && <p style={{ color: '#b91c1c' }}>{error}</p>}
      {!loading && !error && filteredRuns.length === 0 && <p>No repricing runs yet.</p>}
      {!loading && !error && filteredRuns.length > 0 && (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                <th style={{ textAlign: 'left', padding: '8px 6px', borderBottom: '1px solid #e5e7eb' }}>ID</th>
                <th style={{ textAlign: 'left', padding: '8px 6px', borderBottom: '1px solid #e5e7eb' }}>Status</th>
                <th style={{ textAlign: 'left', padding: '8px 6px', borderBottom: '1px solid #e5e7eb' }}>Created at</th>
                <th style={{ textAlign: 'left', padding: '8px 6px', borderBottom: '1px solid #e5e7eb' }}>Started at</th>
                <th style={{ textAlign: 'left', padding: '8px 6px', borderBottom: '1px solid #e5e7eb' }}>Finished at</th>
                <th style={{ textAlign: 'left', padding: '8px 6px', borderBottom: '1px solid #e5e7eb' }}>Last error</th>
              </tr>
            </thead>
            <tbody>
              {filteredRuns.map((run) => (
                <tr key={run.id}>
                  <td style={{ padding: '8px 6px', borderBottom: '1px solid #f1f5f9' }}>#{run.id}</td>
                  <td style={{ padding: '8px 6px', borderBottom: '1px solid #f1f5f9' }}>{formatStatusLabel(run.status)}</td>
                  <td style={{ padding: '8px 6px', borderBottom: '1px solid #f1f5f9' }}>
                    {new Date(run.created_at).toLocaleString()}
                  </td>
                  <td style={{ padding: '8px 6px', borderBottom: '1px solid #f1f5f9' }}>
                    {run.started_at ? new Date(run.started_at).toLocaleString() : '—'}
                  </td>
                  <td style={{ padding: '8px 6px', borderBottom: '1px solid #f1f5f9' }}>
                    {run.finished_at ? new Date(run.finished_at).toLocaleString() : '—'}
                  </td>
                  <td style={{ padding: '8px 6px', borderBottom: '1px solid #f1f5f9' }}>{run.last_error || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {actionError && <p style={{ color: '#b91c1c', marginTop: 12 }}>{actionError}</p>}
    </section>
  )
}
