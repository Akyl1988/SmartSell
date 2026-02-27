import { useState } from 'react'
import { getHttpErrorInfo } from '../../api/client'
import Button from '../../components/ui/Button'
import Card from '../../components/ui/Card'
import ErrorState from '../../components/ui/ErrorState'
import StatusBadge from '../../components/ui/StatusBadge'
import { useToast } from '../../components/ui/Toast'
import { useAdmin } from '../../hooks/useAdmin'
import formStyles from '../../styles/forms.module.css'
import pageStyles from '../../styles/page.module.css'

export default function OwnerOpsPage() {
  const { runSubscriptionRenew, runCampaignsTask, runCampaignsCleanup, runRepricingTask } = useAdmin()
  const { push } = useToast()

  const [renewStatus, setRenewStatus] = useState<string | null>(null)
  const [renewError, setRenewError] = useState<string | null>(null)
  const [renewLoading, setRenewLoading] = useState(false)

  const [preorderError, setPreorderError] = useState<string | null>(null)

  const [kaspiCompanyId, setKaspiCompanyId] = useState('')
  const [kaspiError, setKaspiError] = useState<string | null>(null)

  const [campaignCompanyId, setCampaignCompanyId] = useState('')
  const [campaignStatus, setCampaignStatus] = useState<string | null>(null)
  const [campaignError, setCampaignError] = useState<string | null>(null)
  const [campaignLoading, setCampaignLoading] = useState(false)

  const [cleanupStatus, setCleanupStatus] = useState<string | null>(null)
  const [cleanupError, setCleanupError] = useState<string | null>(null)
  const [cleanupLoading, setCleanupLoading] = useState(false)
  const [cleanupLimit, setCleanupLimit] = useState(500)
  const [cleanupDoneDays, setCleanupDoneDays] = useState(14)
  const [cleanupFailedDays, setCleanupFailedDays] = useState(30)

  const [repricingCompanyId, setRepricingCompanyId] = useState('')
  const [repricingStatus, setRepricingStatus] = useState<string | null>(null)
  const [repricingError, setRepricingError] = useState<string | null>(null)
  const [repricingLoading, setRepricingLoading] = useState(false)

  async function handleRenew() {
    setRenewLoading(true)
    setRenewError(null)
    try {
      const result = await runSubscriptionRenew()
      setRenewStatus(`Processed: ${result.processed}`)
      push('Subscription renewal queued.', 'success')
    } catch (err) {
      const info = getHttpErrorInfo(err)
      const statusPart = info.status ? ` (status ${info.status})` : ''
      setRenewError(`Failed to run renew${statusPart}: ${info.message}`)
      push('Failed to run subscription renew.', 'danger')
    } finally {
      setRenewLoading(false)
    }
  }

  function handlePreorderCheck() {
    setPreorderError('Preorders E2E check endpoint is not wired yet.')
    push('Preorders E2E check is not available.', 'warning')
  }

  function handleKaspiSync() {
    if (!kaspiCompanyId.trim()) {
      setKaspiError('Enter company_id to trigger sync.')
      return
    }
    setKaspiError('Kaspi sync endpoint is not wired yet.')
    push('Kaspi sync is not available yet.', 'warning')
  }

  async function handleCampaignRun() {
    setCampaignStatus(null)
    setCampaignError(null)
    if (!campaignCompanyId.trim()) {
      setCampaignError('Enter company_id to run campaigns.')
      return
    }
    setCampaignLoading(true)
    try {
      const result = await runCampaignsTask({ companyId: Number(campaignCompanyId), limit: 100, dry_run: false })
      setCampaignStatus(`Queued: ${result.queued}, processed: ${result.processed}`)
      push('Campaign run queued.', 'success')
    } catch (err) {
      const info = getHttpErrorInfo(err)
      const statusPart = info.status ? ` (status ${info.status})` : ''
      setCampaignError(`Failed to run campaigns${statusPart}: ${info.message}`)
      push('Failed to run campaigns.', 'danger')
    } finally {
      setCampaignLoading(false)
    }
  }

  async function handleCampaignCleanup() {
    setCleanupStatus(null)
    setCleanupError(null)
    setCleanupLoading(true)
    try {
      const result = await runCampaignsCleanup({
        limit: cleanupLimit,
        done_days: cleanupDoneDays,
        failed_days: cleanupFailedDays,
      })
      setCleanupStatus(`Deleted: ${result.total_deleted}`)
      push('Campaign cleanup completed.', 'success')
    } catch (err) {
      const info = getHttpErrorInfo(err)
      const statusPart = info.status ? ` (status ${info.status})` : ''
      setCleanupError(`Failed to cleanup campaigns${statusPart}: ${info.message}`)
      push('Failed to cleanup campaigns.', 'danger')
    } finally {
      setCleanupLoading(false)
    }
  }

  async function handleRepricingRun() {
    setRepricingStatus(null)
    setRepricingError(null)
    if (!repricingCompanyId.trim()) {
      setRepricingError('Enter company_id to run repricing.')
      return
    }
    setRepricingLoading(true)
    try {
      const result = await runRepricingTask(Number(repricingCompanyId), false)
      setRepricingStatus(`Run #${result.run_id} (${result.status})`)
      push('Repricing task started.', 'success')
    } catch (err) {
      const info = getHttpErrorInfo(err)
      const statusPart = info.status ? ` (status ${info.status})` : ''
      setRepricingError(`Failed to run repricing${statusPart}: ${info.message}`)
      push('Failed to run repricing.', 'danger')
    } finally {
      setRepricingLoading(false)
    }
  }

  return (
    <section className={pageStyles.page}>
      <div className={pageStyles.pageHeader}>
        <div>
          <h1 className={pageStyles.pageTitle}>Operations</h1>
          <p className={pageStyles.pageDescription}>Manual actions and diagnostics for the platform.</p>
        </div>
      </div>

      <div className={pageStyles.section}>
        <Card title="Subscriptions">
          <Button type="button" onClick={handleRenew} disabled={renewLoading}>
            {renewLoading ? 'Running...' : 'Run subscription renewal'}
          </Button>
          {renewStatus && <StatusBadge tone="success" label={renewStatus} />}
          {renewError && <ErrorState message={renewError} />}
        </Card>

        <Card title="Campaigns run" description="Trigger campaign processing for a company.">
          <div className={pageStyles.inline}>
            <input
              className={formStyles.input}
              placeholder="company_id"
              value={campaignCompanyId}
              onChange={(event) => setCampaignCompanyId(event.target.value)}
            />
            <Button type="button" onClick={handleCampaignRun} disabled={campaignLoading}>
              {campaignLoading ? 'Running...' : 'Run'}
            </Button>
          </div>
          {campaignStatus && <StatusBadge tone="success" label={campaignStatus} />}
          {campaignError && <ErrorState message={campaignError} />}
        </Card>

        <Card title="Campaigns cleanup" description="Delete completed or failed campaigns.">
          <div className={pageStyles.inline}>
            <input
              className={formStyles.input}
              type="number"
              value={cleanupLimit}
              onChange={(event) => setCleanupLimit(Number(event.target.value || 1))}
              placeholder="limit"
              min={1}
              max={5000}
            />
            <input
              className={formStyles.input}
              type="number"
              value={cleanupDoneDays}
              onChange={(event) => setCleanupDoneDays(Number(event.target.value || 1))}
              placeholder="done days"
              min={1}
              max={365}
            />
            <input
              className={formStyles.input}
              type="number"
              value={cleanupFailedDays}
              onChange={(event) => setCleanupFailedDays(Number(event.target.value || 1))}
              placeholder="failed days"
              min={1}
              max={365}
            />
            <Button type="button" onClick={handleCampaignCleanup} disabled={cleanupLoading}>
              {cleanupLoading ? 'Running...' : 'Cleanup'}
            </Button>
          </div>
          {cleanupStatus && <StatusBadge tone="success" label={cleanupStatus} />}
          {cleanupError && <ErrorState message={cleanupError} />}
        </Card>

        <Card title="Preorders checks">
          <Button type="button" variant="ghost" onClick={handlePreorderCheck}>
            Run E2E preorder check
          </Button>
          {preorderError && <ErrorState message={preorderError} />}
        </Card>

        <Card title="Kaspi sync">
          <div className={pageStyles.inline}>
            <input
              className={formStyles.input}
              placeholder="company_id"
              value={kaspiCompanyId}
              onChange={(event) => setKaspiCompanyId(event.target.value)}
            />
            <Button type="button" variant="ghost" onClick={handleKaspiSync}>
              Run sync
            </Button>
          </div>
          {kaspiError && <ErrorState message={kaspiError} />}
        </Card>

        <Card title="Repricing task" description="Trigger repricing for a single company.">
          <div className={pageStyles.inline}>
            <input
              className={formStyles.input}
              placeholder="company_id"
              value={repricingCompanyId}
              onChange={(event) => setRepricingCompanyId(event.target.value)}
            />
            <Button type="button" onClick={handleRepricingRun} disabled={repricingLoading}>
              {repricingLoading ? 'Running...' : 'Run repricing'}
            </Button>
          </div>
          {repricingStatus && <StatusBadge tone="success" label={repricingStatus} />}
          {repricingError && <ErrorState message={repricingError} />}
        </Card>
      </div>
    </section>
  )
}