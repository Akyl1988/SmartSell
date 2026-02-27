import { useCallback, useEffect, useMemo, useState } from 'react'
import { cancelPreorder, confirmPreorder, fulfillPreorder, listPreorders, Preorder } from '../../api/preorders'
import { getHttpErrorInfo } from '../../api/client'
import { getProductStock } from '../../api/products'
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
import formStyles from '../../styles/forms.module.css'

type ActionKind = 'confirm' | 'cancel' | 'fulfill'

type StockSnapshot = {
  stock_quantity?: number | null
  in_stock?: boolean
  low_stock?: boolean
}

type StatusFilter = 'all' | 'new' | 'confirmed' | 'fulfilled' | 'cancelled'

export default function PreordersPage() {
  const { hasPreorders, paymentRequired } = useFeatureGate()
  const { push } = useToast()
  const [items, setItems] = useState<Preorder[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [processingById, setProcessingById] = useState<Record<number, ActionKind | undefined>>({})
  const [stockByProductId, setStockByProductId] = useState<Record<number, StockSnapshot>>({})
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [page, setPage] = useState(1)
  const perPage = 20

  const primaryProductIds = useMemo(() => {
    const ids = new Set<number>()
    items.forEach((preorder) => {
      const firstItem = preorder.items?.[0]
      if (firstItem?.product_id) {
        ids.add(firstItem.product_id)
      }
    })
    return Array.from(ids)
  }, [items])

  const loadStock = useCallback(async (productIds: number[]) => {
    if (productIds.length === 0) {
      setStockByProductId({})
      return
    }
    const snapshots: Record<number, StockSnapshot> = {}
    await Promise.all(
      productIds.map(async (productId) => {
        try {
          const data = await getProductStock(productId)
          snapshots[productId] = {
            stock_quantity: (data.stock_quantity as number | null | undefined) ?? null,
            in_stock: Boolean(data.in_stock),
            low_stock: Boolean(data.low_stock),
          }
        } catch {
          snapshots[productId] = { stock_quantity: null }
        }
      })
    )
    setStockByProductId(snapshots)
  }, [])

  const loadPreorders = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await listPreorders({
        page,
        per_page: perPage,
        status: statusFilter === 'all' ? undefined : statusFilter,
      })
      setItems(data.items)
      setTotal(data.total)
    } catch (err) {
      const info = getHttpErrorInfo(err)
      const statusPart = info.status ? ` (status ${info.status})` : ''
      setError(`Failed to load preorders${statusPart}: ${info.message}`)
    } finally {
      setLoading(false)
    }
  }, [page, perPage, statusFilter])

  useEffect(() => {
    if (!hasPreorders || paymentRequired) return
    loadPreorders()
  }, [hasPreorders, paymentRequired, loadPreorders])

  useEffect(() => {
    if (!hasPreorders || paymentRequired) return
    loadStock(primaryProductIds)
  }, [hasPreorders, paymentRequired, loadStock, primaryProductIds])

  if (!hasPreorders || paymentRequired) {
    return (
      <section className={pageStyles.page}>
        <div className={pageStyles.pageHeader}>
          <div>
            <h1 className={pageStyles.pageTitle}>Preorders</h1>
            <p className={pageStyles.pageDescription}>Preorders are available only on the Pro plan.</p>
          </div>
        </div>
      </section>
    )
  }

  function setProcessing(id: number, action?: ActionKind) {
    setProcessingById((prev) => ({ ...prev, [id]: action }))
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

  function getStatusTone(status: string) {
    const normalized = status.trim().toLowerCase()
    if (normalized === 'confirmed') return 'info'
    if (normalized === 'fulfilled') return 'success'
    if (normalized === 'canceled' || normalized === 'cancelled') return 'danger'
    if (normalized === 'pending' || normalized === 'new') return 'warning'
    return 'neutral'
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
      const firstItem = updated.items?.[0]
      if (firstItem?.product_id) {
        await loadStock([firstItem.product_id])
      }
    } catch (err) {
      const info = getHttpErrorInfo(err)
      const statusPart = info.status ? ` (status ${info.status})` : ''
      push(`Failed to ${action} preorder${statusPart}: ${info.message}`, 'danger')
    } finally {
      setProcessing(id, undefined)
    }
  }

  const totalPages = Math.max(1, Math.ceil(total / perPage))

  return (
    <section className={pageStyles.page}>
      <div className={pageStyles.pageHeader}>
        <div>
          <h1 className={pageStyles.pageTitle}>Preorders</h1>
          <p className={pageStyles.pageDescription}>Manage incoming preorders and inventory reservations.</p>
        </div>
        <div className={pageStyles.pageActions}>
          <Button variant="ghost" onClick={loadPreorders} disabled={loading}>
            Refresh
          </Button>
        </div>
      </div>

      <Card>
        <div className={pageStyles.toolbar}>
          <div className={formStyles.formRow}>
            <label className={formStyles.label}>Status</label>
            <select
              className={formStyles.select}
              value={statusFilter}
              onChange={(event) => {
                setStatusFilter(event.target.value as StatusFilter)
                setPage(1)
              }}
            >
              <option value="all">All</option>
              <option value="new">New</option>
              <option value="confirmed">Confirmed</option>
              <option value="fulfilled">Fulfilled</option>
              <option value="cancelled">Cancelled</option>
            </select>
          </div>
          <div className={pageStyles.inline}>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setPage((prev) => Math.max(1, prev - 1))}
              disabled={page <= 1 || loading}
            >
              Prev
            </Button>
            <span className={pageStyles.muted}>
              Page {page} of {totalPages}
            </span>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setPage((prev) => Math.min(totalPages, prev + 1))}
              disabled={page >= totalPages || loading}
            >
              Next
            </Button>
          </div>
        </div>

        {loading && <Loader label="Loading preorders..." />}
        {error && <ErrorState message={error} onRetry={loadPreorders} />}
        {!loading && !error && items.length === 0 && (
          <EmptyState title="No preorders" description="New preorders will appear here once created." />
        )}

        {!loading && !error && items.length > 0 && (
          <div className={pageStyles.tableWrap}>
            <Table>
              <TableHead>
                <TableRow>
                  <TableHeaderCell>Preorder ID</TableHeaderCell>
                  <TableHeaderCell>Product</TableHeaderCell>
                  <TableHeaderCell>Qty</TableHeaderCell>
                  <TableHeaderCell>Stock</TableHeaderCell>
                  <TableHeaderCell>Status</TableHeaderCell>
                  <TableHeaderCell>Created at</TableHeaderCell>
                  <TableHeaderCell>Actions</TableHeaderCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {items.map((preorder) => {
                  const processing = processingById[preorder.id]
                  const { isCanceled, isFulfilled, isConfirmed } = getStatusFlags(preorder.status)
                  const canConfirm = !isCanceled && !isFulfilled && !isConfirmed
                  const canCancel = !isCanceled && !isFulfilled
                  const canFulfill = isConfirmed && !isFulfilled && !isCanceled
                  const firstItem = preorder.items?.[0]
                  const productId = firstItem?.product_id
                  const stockSnapshot = productId ? stockByProductId[productId] : undefined
                  const stockLabel = stockSnapshot?.stock_quantity ?? '—'

                  return (
                    <TableRow key={preorder.id}>
                      <TableCell>#{preorder.id}</TableCell>
                      <TableCell>{getProductLabel(preorder)}</TableCell>
                      <TableCell>{getTotalQty(preorder)}</TableCell>
                      <TableCell>
                        <StatusBadge
                          tone={
                            stockSnapshot?.low_stock
                              ? 'warning'
                              : stockSnapshot?.in_stock
                              ? 'info'
                              : 'neutral'
                          }
                          label={String(stockLabel)}
                        />
                      </TableCell>
                      <TableCell>
                        <StatusBadge tone={getStatusTone(preorder.status)} label={preorder.status} />
                      </TableCell>
                      <TableCell>{new Date(preorder.created_at).toLocaleString()}</TableCell>
                      <TableCell>
                        <div className={pageStyles.inline}>
                          <Button
                            size="sm"
                            variant="primary"
                            onClick={() => handleAction(preorder.id, 'confirm')}
                            disabled={!canConfirm || !!processing}
                          >
                            {processing === 'confirm' ? 'Processing...' : 'Confirm'}
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => handleAction(preorder.id, 'cancel')}
                            disabled={!canCancel || !!processing}
                          >
                            {processing === 'cancel' ? 'Processing...' : 'Cancel'}
                          </Button>
                          <Button
                            size="sm"
                            variant="danger"
                            onClick={() => handleAction(preorder.id, 'fulfill')}
                            disabled={!canFulfill || !!processing}
                          >
                            {processing === 'fulfill' ? 'Processing...' : 'Fulfill'}
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          </div>
        )}
      </Card>
    </section>
  )
}
