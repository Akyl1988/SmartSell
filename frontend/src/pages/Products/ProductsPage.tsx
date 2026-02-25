import { useCallback, useEffect, useMemo, useState } from 'react'
import { getHttpErrorInfo } from '../../api/client'
import { listProducts, ProductListParams, ProductResponse } from '../../api/products'

export default function ProductsPage() {
  const [items, setItems] = useState<ProductResponse[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
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
    <section>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
        <h1>Products</h1>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <input
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search by name or SKU"
          />
          <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value as 'all' | 'active' | 'inactive')}>
            <option value="all">All statuses</option>
            <option value="active">Active</option>
            <option value="inactive">Inactive</option>
          </select>
          <button
            onClick={() => {
              try {
                setActionError(null)
                exportProductsToCsv(filteredItems)
              } catch (err) {
                const info = getHttpErrorInfo(err)
                console.error('Failed to export products', err)
                setActionError(`Failed to export products: ${info.message}`)
              }
            }}
            disabled={loading || filteredItems.length === 0 || error !== null}
          >
            Export CSV
          </button>
          <button
            onClick={() => {
              setActionError(null)
              loadProducts()
            }}
            disabled={loading}
          >
            Refresh
          </button>
        </div>
      </div>
      {loading && <p>Loading products...</p>}
      {error && <p style={{ color: '#b91c1c' }}>{error}</p>}
      {!loading && !error && filteredItems.length === 0 && <p>No products yet.</p>}
      {!loading && !error && filteredItems.length > 0 && (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                <th style={{ textAlign: 'left', padding: '8px 6px', borderBottom: '1px solid #e5e7eb' }}>ID</th>
                <th style={{ textAlign: 'left', padding: '8px 6px', borderBottom: '1px solid #e5e7eb' }}>Name</th>
                <th style={{ textAlign: 'left', padding: '8px 6px', borderBottom: '1px solid #e5e7eb' }}>SKU</th>
                <th style={{ textAlign: 'left', padding: '8px 6px', borderBottom: '1px solid #e5e7eb' }}>Price</th>
                <th style={{ textAlign: 'left', padding: '8px 6px', borderBottom: '1px solid #e5e7eb' }}>Stock</th>
                <th style={{ textAlign: 'left', padding: '8px 6px', borderBottom: '1px solid #e5e7eb' }}>Status</th>
                <th style={{ textAlign: 'left', padding: '8px 6px', borderBottom: '1px solid #e5e7eb' }}>Updated at</th>
              </tr>
            </thead>
            <tbody>
              {filteredItems.map((product) => (
                <tr key={product.id}>
                  <td style={{ padding: '8px 6px', borderBottom: '1px solid #f1f5f9' }}>#{product.id}</td>
                  <td style={{ padding: '8px 6px', borderBottom: '1px solid #f1f5f9' }}>{product.name}</td>
                  <td style={{ padding: '8px 6px', borderBottom: '1px solid #f1f5f9' }}>{product.sku}</td>
                  <td style={{ padding: '8px 6px', borderBottom: '1px solid #f1f5f9' }}>{product.price ?? '—'}</td>
                  <td style={{ padding: '8px 6px', borderBottom: '1px solid #f1f5f9' }}>
                    {product.stock_quantity ?? '—'}
                  </td>
                  <td style={{ padding: '8px 6px', borderBottom: '1px solid #f1f5f9' }}>
                    {product.is_active === false ? 'Inactive' : 'Active'}
                  </td>
                  <td style={{ padding: '8px 6px', borderBottom: '1px solid #f1f5f9' }}>
                    {new Date(product.updated_at).toLocaleString()}
                  </td>
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
