import { useState } from 'react'
import { apiClient, getHttpErrorInfo } from '../api/client'
import Button from '../components/ui/Button'
import Card from '../components/ui/Card'
import ErrorState from '../components/ui/ErrorState'
import StatusBadge from '../components/ui/StatusBadge'
import pageStyles from '../styles/page.module.css'

type KaspiMethod = 'GET' | 'POST'

const KASPI_BASE = '/api/v1/kaspi'

export default function KaspiPage() {
  const [log, setLog] = useState<string>('')
  const [busyAction, setBusyAction] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function call(method: KaspiMethod, path: string) {
    const actionKey = `${method} ${path}`
    setBusyAction(actionKey)
    setError(null)
    setLog((prev) => `${prev}\n> ${actionKey}`)
    try {
      const { data } = await apiClient.request<string>({
        url: `${KASPI_BASE}${path}`,
        method,
        responseType: 'text',
      })
      setLog((prev) => `${prev}\n${data}\n`)
    } catch (err) {
      const info = getHttpErrorInfo(err)
      const statusPart = info.status ? ` (status ${info.status})` : ''
      setError(`Kaspi request failed${statusPart}: ${info.message}`)
    } finally {
      setBusyAction(null)
    }
  }

  return (
    <section className={pageStyles.page}>
      <div className={pageStyles.pageHeader}>
        <div>
          <h1 className={pageStyles.pageTitle}>Kaspi feed control</h1>
          <p className={pageStyles.pageDescription}>Trigger Kaspi feed generation and monitor response logs.</p>
        </div>
      </div>

      <Card>
        <div className={pageStyles.cardGrid}>
          <Button
            variant="ghost"
            onClick={() => call('GET', '/_debug/ping')}
            disabled={busyAction !== null}
          >
            Ping module
          </Button>
          <Button
            variant="ghost"
            onClick={() => call('GET', '/health/MyKaspiShop')}
            disabled={busyAction !== null}
          >
            Health check
          </Button>
          <Button onClick={() => call('POST', '/feed/generate')} disabled={busyAction !== null}>
            Generate feed
          </Button>
          <Button variant="ghost" onClick={() => call('POST', '/feed/upload')} disabled={busyAction !== null}>
            Upload feed
          </Button>
          <Button variant="ghost" onClick={() => call('GET', '/import/status')} disabled={busyAction !== null}>
            Import status
          </Button>
        </div>
        {busyAction && <StatusBadge tone="info" label={`Running: ${busyAction}`} />}
        {error && <ErrorState message={error} />}
      </Card>

      <Card title="Logs">
        <pre className={pageStyles.codeBlock}>{log || 'Logs will appear here...'}</pre>
      </Card>
    </section>
  )
}