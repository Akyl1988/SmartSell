import { useState } from 'react'
import { getHttpErrorInfo } from '../../api/client'
import {
  downloadInventoryReport,
  downloadPreordersReport,
  downloadRepricingRunsReport,
  downloadWalletTransactionsReport,
} from '../../api/reports'

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}

export default function ReportsPage() {
  const [actionError, setActionError] = useState<string | null>(null)

  async function onDownloadPreorders() {
    try {
      setActionError(null)
      const blob = await downloadPreordersReport({ limit: 500 })
      saveBlob(blob, 'preorders.csv')
    } catch (err) {
      const info = getHttpErrorInfo(err)
      const statusPart = info.status ? ` (status ${info.status})` : ''
      setActionError(`Failed to download preorders report${statusPart}: ${info.message}`)
    }
  }

  async function onDownloadInventory() {
    try {
      setActionError(null)
      const blob = await downloadInventoryReport({ limit: 500 })
      saveBlob(blob, 'inventory.csv')
    } catch (err) {
      const info = getHttpErrorInfo(err)
      const statusPart = info.status ? ` (status ${info.status})` : ''
      setActionError(`Failed to download inventory report${statusPart}: ${info.message}`)
    }
  }

  async function onDownloadRepricingRuns() {
    try {
      setActionError(null)
      const blob = await downloadRepricingRunsReport({ limit: 500 })
      saveBlob(blob, 'repricing_runs.csv')
    } catch (err) {
      const info = getHttpErrorInfo(err)
      const statusPart = info.status ? ` (status ${info.status})` : ''
      setActionError(`Failed to download repricing runs report${statusPart}: ${info.message}`)
    }
  }

  async function onDownloadWalletTransactions() {
    try {
      setActionError(null)
      const blob = await downloadWalletTransactionsReport({ limit: 500 })
      saveBlob(blob, 'wallet_transactions.csv')
    } catch (err) {
      const info = getHttpErrorInfo(err)
      const statusPart = info.status ? ` (status ${info.status})` : ''
      setActionError(`Failed to download wallet transactions report${statusPart}: ${info.message}`)
    }
  }

  return (
    <section>
      <h1>Reports</h1>
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
        <button onClick={onDownloadPreorders}>Download Preorders CSV</button>
        <button onClick={onDownloadInventory}>Download Inventory CSV</button>
        <button onClick={onDownloadRepricingRuns}>Download Repricing Runs CSV</button>
        <button onClick={onDownloadWalletTransactions}>Download Wallet Transactions CSV</button>
      </div>
      {actionError && <p style={{ color: '#b91c1c', marginTop: 12 }}>{actionError}</p>}
    </section>
  )
}
