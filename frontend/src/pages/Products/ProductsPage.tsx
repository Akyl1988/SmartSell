import { useCallback, useEffect, useMemo, useState } from 'react'
import { getHttpErrorInfo } from '../../api/client'
import { listProducts, ProductListParams, ProductResponse } from '../../api/products'
import Button from '../../components/ui/Button'
import Card from '../../components/ui/Card'
import EmptyState from '../../components/ui/EmptyState'
import ErrorState from '../../components/ui/ErrorState'
import Loader from '../../components/ui/Loader'
import StatusBadge from '../../components/ui/StatusBadge'
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from '../../components/ui/Table'
import { useToast } from '../../components/ui/Toast'
import formStyles from '../../styles/forms.module.css'
import pageStyles from '../../styles/page.module.css'

export default function ProductsPage() {
  const { push } = useToast()
  const [items, setItems] = useState<ProductResponse[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState<'all' | 'active' | 'inactive'>('all')

  const searchTerm = search.trim()

  const loadProducts = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params: ProductListParams = { page: 1, per_page: 50 }
      if (searchTerm) {
        params.search = searchTerm
      }
      if (statusFilter !== 'all') {
        params.is_active = statusFilter === 'active'
      }
      const data = await listProducts(params)
      setItems(data)
    } catch (err) {
      const info = getHttpErrorInfo(err)
      const statusPart = info.status ? ` (status ${info.status})` : ''
      setError(`Failed to load products${statusPart}: ${info.message}`)
    } finally {
      setLoading(false)
    }
  }, [searchTerm, statusFilter])

  useEffect(() => {
    loadProducts()
  }, [loadProducts])

  const filteredItems = useMemo(() => {
    let result = items
    if (statusFilter !== 'all') {
      const target = statusFilter === 'active'
      result = result.filter((product) => (product.is_active ?? true) === target)
    }
    if (searchTerm) {
      const query = searchTerm.toLowerCase()
      result = result.filter((product) => {
        const nameMatch = product.name.toLowerCase().includes(query)
        const skuMatch = product.sku.toLowerCase().includes(query)
        return nameMatch || skuMatch
      })
    }
    return result
  }, [items, searchTerm, statusFilter])

  function formatTimestampForFile(date: Date) {
    const year = date.getFullYear()
    const month = String(date.getMonth() + 1).padStart(2, '0')
    const day = String(date.getDate()).padStart(2, '0')
    const hour = String(date.getHours()).padStart(2, '0')
    const minute = String(date.getMinutes()).padStart(2, '0')
    return `${year}${month}${day}-${hour}${minute}`
  }

  function escapeCsvValue(value: string | number | boolean | null | undefined) {
    if (value === null || value === undefined) return ''
    const stringValue = String(value)
    if (/[",\n\r]/.test(stringValue)) {
      return `"${stringValue.replace(/"/g, '""')}"`
    }
    return stringValue
  }

  function exportProductsToCsv(products: ProductResponse[]) {
    const header = ['id', 'name', 'sku', 'price', 'stock_quantity', 'reserved_quantity', 'updated_at']
    const rows = products.map((product) => [
      product.id,
      product.name,
      product.sku,
      product.price,
      product.stock_quantity ?? '',
      '',
      product.updated_at,
    ])

    const csvLines = [header, ...rows]
      .map((row) => row.map((value) => escapeCsvValue(value)).join(','))
      .join('\n')

    const blob = new Blob([csvLines], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const timestamp = formatTimestampForFile(new Date())
    const link = document.createElement('a')
    link.href = url
    link.download = `products-${timestamp}.csv`
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    URL.revokeObjectURL(url)
  }

  return (
    <section className={pageStyles.page}>
      <div className={pageStyles.pageHeader}>
        <div>
          <h1 className={pageStyles.pageTitle}>Products</h1>
          <p className={pageStyles.pageDescription}>Manage product availability and inventory.</p>
        </div>
        <div className={pageStyles.pageActions}>
          <Button
            variant="ghost"
            onClick={() => {
              try {
                exportProductsToCsv(filteredItems)
              } catch (err) {
                const info = getHttpErrorInfo(err)
                push(`Failed to export products: ${info.message}`, 'danger')
              }
            }}
            disabled={loading || filteredItems.length === 0 || error !== null}
          >
            Export CSV
          </Button>
          <Button variant="ghost" onClick={loadProducts} disabled={loading}>
            Refresh
          </Button>
        </div>
      </div>

      <Card>
        <div className={pageStyles.toolbar}>
          <div className={formStyles.formRow}>
            <label className={formStyles.label}>Search</label>
            <input
              className={formStyles.input}
              type="search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search by name or SKU"
            />
          </div>
          <div className={formStyles.formRow}>
            <label className={formStyles.label}>Status</label>
            <select
              className={formStyles.select}
              value={statusFilter}
              onChange={(event) => setStatusFilter(event.target.value as 'all' | 'active' | 'inactive')}
            >
              <option value="all">All statuses</option>
              <option value="active">Active</option>
              <option value="inactive">Inactive</option>
            </select>
          </div>
        </div>

        {loading && <Loader label="Loading products..." />}
        {error && <ErrorState message={error} onRetry={loadProducts} />}
        {!loading && !error && filteredItems.length === 0 && (
          <EmptyState title="No products" description="No products matched your search." />
        )}

        {!loading && !error && filteredItems.length > 0 && (
          <div className={pageStyles.tableWrap}>
            <Table>
              <TableHead>
                <TableRow>
                  <TableHeaderCell>ID</TableHeaderCell>
                  <TableHeaderCell>Name</TableHeaderCell>
                  <TableHeaderCell>SKU</TableHeaderCell>
                  <TableHeaderCell>Price</TableHeaderCell>
                  <TableHeaderCell>Stock</TableHeaderCell>
                  <TableHeaderCell>Status</TableHeaderCell>
                  <TableHeaderCell>Updated at</TableHeaderCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {filteredItems.map((product) => (
                  <TableRow key={product.id}>
                    <TableCell>#{product.id}</TableCell>
                    <TableCell>{product.name}</TableCell>
                    <TableCell>{product.sku}</TableCell>
                    <TableCell>{product.price ?? '—'}</TableCell>
                    <TableCell>{product.stock_quantity ?? '—'}</TableCell>
                    <TableCell>
                      <StatusBadge
                        tone={product.is_active === false ? 'warning' : 'success'}
                        label={product.is_active === false ? 'Inactive' : 'Active'}
                      />
                    </TableCell>
                    <TableCell>{new Date(product.updated_at).toLocaleString()}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </Card>
    </section>
  )
}