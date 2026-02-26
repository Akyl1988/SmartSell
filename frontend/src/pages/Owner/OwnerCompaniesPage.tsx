import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { CompanyListItem, getCompanies } from '../../api/admin'
import { getHttpErrorInfo } from '../../api/client'

const pageStyle: React.CSSProperties = {
  background: '#f3f4f6',
  minHeight: '100%',
  padding: '24px',
}

const panelStyle: React.CSSProperties = {
  background: '#ffffff',
  borderRadius: 8,
  padding: 16,
  boxShadow: '0 1px 2px rgba(15, 23, 42, 0.08)',
}

const tableStyle: React.CSSProperties = {
  width: '100%',
  borderCollapse: 'collapse',
  marginTop: 12,
}

const thStyle: React.CSSProperties = {
  textAlign: 'left',
  fontSize: 12,
  color: '#64748b',
  padding: '10px 8px',
  borderBottom: '1px solid #e2e8f0',
}

const tdStyle: React.CSSProperties = {
  padding: '10px 8px',
  borderBottom: '1px solid #e2e8f0',
  fontSize: 14,
}

export default function OwnerCompaniesPage() {
  const navigate = useNavigate()
  const [items, setItems] = useState<CompanyListItem[]>([])
  const [page, setPage] = useState(1)
  const [size] = useState(20)
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  function loadCompanies(nextPage: number, nextQuery: string) {
    setLoading(true)
    getCompanies({ page: nextPage, size, q: nextQuery })
      .then((data) => {
        setItems(data.items)
        setError(null)
      })
      .catch((err) => {
        const info = getHttpErrorInfo(err)
        const statusPart = info.status ? ` (status ${info.status})` : ''
        setError(`Failed to load companies${statusPart}: ${info.message}`)
      })
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    loadCompanies(page, query)
  }, [page])

  function onSearchSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setPage(1)
    loadCompanies(1, query)
  }

  function handleRowClick(companyId: number) {
    navigate(`/owner/companies/${companyId}`)
  }

  return (
    <section style={pageStyle}>
      <div style={{ marginBottom: 16 }}>
        <h1 style={{ marginBottom: 6 }}>Магазины</h1>
        <p style={{ color: '#64748b' }}>Список компаний и текущие планы.</p>
      </div>

      <div style={panelStyle}>
        <form onSubmit={onSearchSubmit} style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          <input
            type="text"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Поиск по имени или BIN/IIN"
            style={{ flex: '1 1 240px', padding: '8px 10px', borderRadius: 6, border: '1px solid #cbd5f5' }}
          />
          <button type="submit" style={{ background: '#2563eb', color: '#fff', border: 'none', borderRadius: 6 }}>
            Найти
          </button>
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
            <button
              type="button"
              onClick={() => setPage((prev) => Math.max(1, prev - 1))}
              disabled={page <= 1}
            >
              Назад
            </button>
            <span style={{ fontSize: 12, color: '#64748b' }}>Стр. {page}</span>
            <button type="button" onClick={() => setPage((prev) => prev + 1)}>
              Далее
            </button>
          </div>
        </form>

        {loading && <p style={{ marginTop: 16 }}>Загрузка...</p>}
        {error && <p style={{ marginTop: 16, color: '#b91c1c' }}>{error}</p>}

        {!loading && !error && (
          <table style={tableStyle}>
            <thead>
              <tr>
                <th style={thStyle}>ID</th>
                <th style={thStyle}>Название</th>
                <th style={thStyle}>BIN/IIN</th>
                <th style={thStyle}>Kaspi Store ID</th>
                <th style={thStyle}>План</th>
                <th style={thStyle}>Статус</th>
                <th style={thStyle}>План до</th>
              </tr>
            </thead>
            <tbody>
              {items.map((company) => (
                <tr
                  key={company.id}
                  onClick={() => handleRowClick(company.id)}
                  style={{ cursor: 'pointer' }}
                >
                  <td style={tdStyle}>{company.id}</td>
                  <td style={tdStyle}>{company.name}</td>
                  <td style={tdStyle}>{company.bin_iin ?? '—'}</td>
                  <td style={tdStyle}>{company.kaspi_store_id ?? '—'}</td>
                  <td style={tdStyle}>{company.current_plan ?? '—'}</td>
                  <td style={tdStyle}>{company.is_active ? 'Активна' : 'Неактивна'}</td>
                  <td style={tdStyle}>{company.plan_expires_at ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  )
}
