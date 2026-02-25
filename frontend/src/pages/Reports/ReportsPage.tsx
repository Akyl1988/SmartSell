import {
  downloadInventoryReport,
  downloadPreordersReport,
  downloadRepricingRunsReport,
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
  async function onDownloadPreorders() {
    const blob = await downloadPreordersReport({ limit: 500 })
    saveBlob(blob, 'preorders.csv')
  }

  async function onDownloadInventory() {
    const blob = await downloadInventoryReport({ limit: 500 })
    saveBlob(blob, 'inventory.csv')
  }

  async function onDownloadRepricingRuns() {
    const blob = await downloadRepricingRunsReport({ limit: 500 })
    saveBlob(blob, 'repricing_runs.csv')
  }

  return (
    <section>
      <h1>Reports</h1>
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
        <button onClick={onDownloadPreorders}>Download Preorders CSV</button>
        <button onClick={onDownloadInventory}>Download Inventory CSV</button>
        <button onClick={onDownloadRepricingRuns}>Download Repricing Runs CSV</button>
      </div>
    </section>
  )
}
