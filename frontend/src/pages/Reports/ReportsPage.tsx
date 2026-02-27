import { useState } from 'react'
import { getHttpErrorInfo } from '../../api/client'
import {
  downloadInventoryReport,
  downloadPreordersReport,
  downloadRepricingRunsReport,
  downloadWalletTransactionsReport,
} from '../../api/reports'
import Button from '../../components/ui/Button'
import Card from '../../components/ui/Card'
import ErrorState from '../../components/ui/ErrorState'
import { useToast } from '../../components/ui/Toast'
import pageStyles from '../../styles/page.module.css'

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}

export default function ReportsPage() {
  const { push } = useToast()
  const [actionError, setActionError] = useState<string | null>(null)

  async function onDownloadPreorders() {
    try {
      setActionError(null)
      const blob = await downloadPreordersReport({ limit: 500 })
      saveBlob(blob, 'preorders.csv')
      push('Preorders report downloaded.', 'success')
    } catch (err) {
      const info = getHttpErrorInfo(err)
      const statusPart = info.status ? ` (status ${info.status})` : ''
      setActionError(`Failed to download preorders report${statusPart}: ${info.message}`)
      push('Failed to download preorders report.', 'danger')
    }
  }

  async function onDownloadInventory() {
    try {
      setActionError(null)
      const blob = await downloadInventoryReport({ limit: 500 })
      saveBlob(blob, 'inventory.csv')
      push('Inventory report downloaded.', 'success')
    } catch (err) {
      const info = getHttpErrorInfo(err)
      const statusPart = info.status ? ` (status ${info.status})` : ''
      setActionError(`Failed to download inventory report${statusPart}: ${info.message}`)
      push('Failed to download inventory report.', 'danger')
    }
  }

  async function onDownloadRepricingRuns() {
    try {
      setActionError(null)
      const blob = await downloadRepricingRunsReport({ limit: 500 })
      saveBlob(blob, 'repricing_runs.csv')
      push('Repricing runs report downloaded.', 'success')
    } catch (err) {
      const info = getHttpErrorInfo(err)
      const statusPart = info.status ? ` (status ${info.status})` : ''
      setActionError(`Failed to download repricing runs report${statusPart}: ${info.message}`)
      push('Failed to download repricing runs report.', 'danger')
    }
  }

  async function onDownloadWalletTransactions() {
    try {
      setActionError(null)
      const blob = await downloadWalletTransactionsReport({ limit: 500 })
      saveBlob(blob, 'wallet_transactions.csv')
      push('Wallet transactions report downloaded.', 'success')
    } catch (err) {
      const info = getHttpErrorInfo(err)
      const statusPart = info.status ? ` (status ${info.status})` : ''
      setActionError(`Failed to download wallet transactions report${statusPart}: ${info.message}`)
      push('Failed to download wallet transactions report.', 'danger')
    }
  }

  return (
    <section className={pageStyles.page}>
      <div className={pageStyles.pageHeader}>
        <div>
          <h1 className={pageStyles.pageTitle}>Reports</h1>
          <p className={pageStyles.pageDescription}>Export operational data as CSV reports.</p>
        </div>
      </div>

      <Card>
        <div className={pageStyles.inline}>
          <Button onClick={onDownloadPreorders}>Preorders CSV</Button>
          <Button variant="ghost" onClick={onDownloadInventory}>
            Inventory CSV
          </Button>
          <Button variant="ghost" onClick={onDownloadRepricingRuns}>
            Repricing runs CSV
          </Button>
          <Button variant="ghost" onClick={onDownloadWalletTransactions}>
            Wallet transactions CSV
          </Button>
        </div>
        {actionError && <ErrorState message={actionError} />}
      </Card>
    </section>
  )
}