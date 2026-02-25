import { useEffect, useState } from 'react'
import {
  cancelPreorder,
  confirmPreorder,
  fulfillPreorder,
  listPreorders,
  Preorder,
} from '../../api/preorders'
import { getHttpErrorInfo } from '../../api/client'
import { useFeatureGate } from '../../hooks/useFeatureGate'

type ActionKind = 'confirm' | 'cancel' | 'fulfill'

export default function PreordersPage() {
  const { hasPreorders, paymentRequired } = useFeatureGate()
  const [items, setItems] = useState<Preorder[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [processingById, setProcessingById] = useState<Record<number, ActionKind | undefined>>({})

  useEffect(() => {
    if (!hasPreorders || paymentRequired) return
    setLoading(true)
    setError(null)
    listPreorders()
      .then((res) => setItems(res))
      .catch((err) => {
        const info = getHttpErrorInfo(err)
        const statusPart = info.status ? ` (status ${info.status})` : ''
        setError(`Failed to load preorders${statusPart}: ${info.message}`)
      })
      .finally(() => {
        setLoading(false)
      })
  }, [hasPreorders, paymentRequired])

  if (!hasPreorders || paymentRequired) {
    return (
      <section>
        <h1>Preorders</h1>
        <p>Preorders are available only on the Pro plan.</p>
      </section>
    )
  }

  function setProcessing(id: number, action?: ActionKind) {
    setProcessingById((prev) => ({ ...prev, [id]: action }))
  }

  function formatActionError(action: ActionKind, status?: number, message?: string) {
    const statusPart = status ? ` (status ${status})` : ''
    const detail = message && message.trim().length > 0 ? message : 'Unknown error'
    return `Failed to ${action} preorder${statusPart}: ${detail}`
  }

  function getProductLabel(preorder: Preorder) {
    if (!preorder.items || preorder.items.length === 0) return 'Unknown'
    const first = preorder.items[0]
    const name = first.name || first.sku || (first.product_id ? `#${first.product_id}` : 'Unknown')
    if (preorder.items.length > 1) {
      return `${name} +${preorder.items.length - 1} more`
    }
    return name
  }

  function getTotalQty(preorder: Preorder) {
    if (!preorder.items || preorder.items.length === 0) return 0
    return preorder.items.reduce((sum, item) => sum + item.qty, 0)
  }

  function getStatusFlags(status: string) {
    const normalized = status.trim().toLowerCase()
    const isCanceled = normalized === 'canceled' || normalized === 'cancelled'
    const isFulfilled = normalized === 'fulfilled'
    const isConfirmed = normalized === 'confirmed'
    return { isCanceled, isFulfilled, isConfirmed }
  }

  async function handleAction(id: number, action: ActionKind) {
    setProcessing(id, action)
    setActionError(null)
    try {
      let updated: Preorder
      if (action === 'confirm') {
        updated = await confirmPreorder(id)
      } else if (action === 'cancel') {
        updated = await cancelPreorder(id)
      } else {
        updated = await fulfillPreorder(id)
      }

      setItems((prev) => prev.map((p) => (p.id === id ? updated : p)))
    } catch (err) {
      const info = getHttpErrorInfo(err)
      console.error(`Failed to ${action} preorder`, err)
      setActionError(formatActionError(action, info.status, info.message))
    } finally {
      setProcessing(id, undefined)
    }
  }

  return (
    <section>
      <h1>Preorders</h1>
      {loading && <p>Loading preorders...</p>}
      {error && <p style={{ color: '#b91c1c' }}>{error}</p>}
      {!loading && !error && items.length === 0 && <p>No preorders yet.</p>}
      {!loading && !error && items.length > 0 && (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                <th style={{ textAlign: 'left', padding: '8px 6px', borderBottom: '1px solid #e5e7eb' }}>ID</th>
                <th style={{ textAlign: 'left', padding: '8px 6px', borderBottom: '1px solid #e5e7eb' }}>Product</th>
                <th style={{ textAlign: 'left', padding: '8px 6px', borderBottom: '1px solid #e5e7eb' }}>Qty</th>
                <th style={{ textAlign: 'left', padding: '8px 6px', borderBottom: '1px solid #e5e7eb' }}>Status</th>
                <th style={{ textAlign: 'left', padding: '8px 6px', borderBottom: '1px solid #e5e7eb' }}>Created at</th>
                <th style={{ textAlign: 'left', padding: '8px 6px', borderBottom: '1px solid #e5e7eb' }}>Updated at</th>
                <th style={{ textAlign: 'left', padding: '8px 6px', borderBottom: '1px solid #e5e7eb' }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {items.map((preorder) => {
                const processing = processingById[preorder.id]
                const { isCanceled, isFulfilled, isConfirmed } = getStatusFlags(preorder.status)
                const canConfirm = !isCanceled && !isFulfilled && !isConfirmed
                const canCancel = !isCanceled && !isFulfilled
                const canFulfill = isConfirmed && !isFulfilled && !isCanceled

                return (
                  <tr key={preorder.id}>
                    <td style={{ padding: '8px 6px', borderBottom: '1px solid #f1f5f9' }}>#{preorder.id}</td>
                    <td style={{ padding: '8px 6px', borderBottom: '1px solid #f1f5f9' }}>
                      {getProductLabel(preorder)}
                    </td>
                    <td style={{ padding: '8px 6px', borderBottom: '1px solid #f1f5f9' }}>{getTotalQty(preorder)}</td>
                    <td style={{ padding: '8px 6px', borderBottom: '1px solid #f1f5f9' }}>{preorder.status}</td>
                    <td style={{ padding: '8px 6px', borderBottom: '1px solid #f1f5f9' }}>
                      {new Date(preorder.created_at).toLocaleString()}
                    </td>
                    <td style={{ padding: '8px 6px', borderBottom: '1px solid #f1f5f9' }}>
                      {new Date(preorder.updated_at).toLocaleString()}
                    </td>
                    <td style={{ padding: '8px 6px', borderBottom: '1px solid #f1f5f9' }}>
                      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                        <button
                          onClick={() => handleAction(preorder.id, 'confirm')}
                          disabled={!canConfirm || !!processing}
                        >
                          {processing === 'confirm' ? 'Processing...' : 'Confirm'}
                        </button>
                        <button
                          onClick={() => handleAction(preorder.id, 'cancel')}
                          disabled={!canCancel || !!processing}
                        >
                          {processing === 'cancel' ? 'Processing...' : 'Cancel'}
                        </button>
                        <button
                          onClick={() => handleAction(preorder.id, 'fulfill')}
                          disabled={!canFulfill || !!processing}
                        >
                          {processing === 'fulfill' ? 'Processing...' : 'Fulfill'}
                        </button>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
      {actionError && <p style={{ color: '#b91c1c', marginTop: 12 }}>{actionError}</p>}
    </section>
  )
}
