import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import apiClient from './api/api.js'

/**
 * SmartSell – App.jsx
 * Современный, расширяемый каркас UI:
 * - Health-индикатор и авто-обновление
 * - Список кампаний (по контракту /api/v1/campaigns?skip&limit)
 * - Поиск/фильтр по названию (клиентский)
 * - Создание/редактирование/удаление
 * - Быстрая смена статуса
 * - Пагинация (skip/limit)
 * - Надёжная обработка ошибок
 * - Готов к переключению: моки (MSW) ↔ реальный бэкенд
 */

const STATUSES = ['DRAFT', 'ACTIVE', 'PAUSED', 'COMPLETED']
const DEFAULT_PAGE_SIZE = 10

// ---------- Утилиты ----------
const toDisplayName = (c) => c?.title ?? c?.name ?? '—'
const toIso = (d) => (d ? new Date(d).toISOString() : null)
const toLocal = (d) =>
  d ? new Date(d).toLocaleString() : '—'

function classNames(...a) {
  return a.filter(Boolean).join(' ')
}

function useDebouncedValue(value, delayMs = 300) {
  const [v, setV] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setV(value), delayMs)
    return () => clearTimeout(t)
  }, [value, delayMs])
  return v
}

// ---------- UI блоки ----------
function Header() {
  return (
    <header
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: 12,
        padding: '16px 20px',
        borderBottom: '1px solid #eee',
        background: '#fff',
        position: 'sticky',
        top: 0,
        zIndex: 10,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
        <h1 style={{ margin: 0, fontSize: 22 }}>SmartSell</h1>
        <span style={{ fontSize: 13, color: '#667' }}>Campaign Manager</span>
      </div>
      <span style={{ fontSize: 12, color: '#888' }}>UI v1.0</span>
    </header>
  )
}

function Status({ health, apiUrl }) {
  const s = (health?.status || '').toLowerCase()
  const isOk = s === 'ok' || s === 'healthy' || s === 'ready' || s === 'up'
  const color = isOk ? '#0a7d2b' : '#b35c00'
  return (
    <section
      style={{
        padding: '12px 20px',
        borderBottom: '1px solid #f2f2f2',
        display: 'flex',
        gap: 24,
        alignItems: 'center',
        flexWrap: 'wrap',
        background: '#fcfcfc',
      }}
    >
      <div>
        <strong>API:</strong>{' '}
        <span style={{ color }}>{health?.status ?? 'Unknown'}</span>
      </div>
      <div style={{ color: '#666' }}>
        <strong>URL:</strong> {apiUrl || '—'}
      </div>
      {health?.version && (
        <div style={{ color: '#666' }}>
          <strong>Backend:</strong> {health.version}
        </div>
      )}
    </section>
  )
}

function Toolbar({
  onCreateClick,
  refreshing,
  onRefresh,
  q,
  onQuery,
  pageSize,
  onChangePageSize,
}) {
  return (
    <div
      style={{
        display: 'flex',
        gap: 10,
        padding: '14px 20px',
        alignItems: 'center',
        flexWrap: 'wrap',
      }}
    >
      <button
        onClick={onCreateClick}
        style={{
          background: '#111827',
          color: '#fff',
          border: 'none',
          padding: '10px 14px',
          borderRadius: 8,
          cursor: 'pointer',
        }}
      >
        + New campaign
      </button>

      <button
        onClick={onRefresh}
        disabled={refreshing}
        style={{
          background: '#fff',
          color: '#111',
          border: '1px solid #ddd',
          padding: '10px 12px',
          borderRadius: 8,
          cursor: 'pointer',
          opacity: refreshing ? 0.6 : 1,
        }}
      >
        {refreshing ? 'Refreshing…' : 'Refresh'}
      </button>

      <div style={{ marginLeft: 'auto', display: 'flex', gap: 10 }}>
        <input
          value={q}
          onChange={(e) => onQuery(e.target.value)}
          placeholder="Search by name…"
          style={{
            minWidth: 220,
            padding: '10px 12px',
            border: '1px solid #ddd',
            borderRadius: 8,
          }}
        />
        <select
          value={pageSize}
          onChange={(e) => onChangePageSize(Number(e.target.value))}
          title="Items per page"
          style={{
            padding: '10px 12px',
            border: '1px solid #ddd',
            borderRadius: 8,
            background: '#fff',
          }}
        >
          {[10, 20, 50, 100].map((n) => (
            <option key={n} value={n}>
              {n}/page
            </option>
          ))}
        </select>
      </div>
    </div>
  )
}

function Card({ children, muted = false }) {
  return (
    <div
      style={{
        border: '1px solid #eee',
        borderRadius: 12,
        padding: 16,
        background: muted ? '#fafafa' : '#fff',
        boxShadow: '0 1px 2px rgba(0,0,0,0.04)',
      }}
    >
      {children}
    </div>
  )
}

function StatusPill({ value }) {
  const v = (value || '').toUpperCase()
  const palette = {
    DRAFT: ['#1f2937', '#e5e7eb'],
    ACTIVE: ['#065f46', '#d1fae5'],
    PAUSED: ['#92400e', '#fde68a'],
    COMPLETED: ['#334155', '#e2e8f0'],
  }
  const [fg, bg] = palette[v] || ['#555', '#f3f4f6']
  return (
    <span
      style={{
        fontSize: 12,
        color: fg,
        background: bg,
        padding: '2px 8px',
        borderRadius: 999,
      }}
      title={v || 'unknown'}
    >
      {v || 'UNKNOWN'}
    </span>
  )
}

function CampaignCard({ c, onEdit, onDelete, onStatus }) {
  const created = useMemo(() => toLocal(c.created_at), [c.created_at])
  const scheduled = useMemo(() => toLocal(c.scheduled_at), [c.scheduled_at])
  const displayName = toDisplayName(c)

  return (
    <Card>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          gap: 12,
          alignItems: 'baseline',
        }}
      >
        <h3 style={{ margin: '0 0 6px 0' }}>{displayName}</h3>
        <StatusPill value={c.status} />
      </div>

      {c.description && (
        <p style={{ margin: '6px 0 10px 0', color: '#444' }}>
          {c.description}
        </p>
      )}

      <div
        style={{
          display: 'flex',
          gap: 18,
          color: '#666',
          fontSize: 13,
          flexWrap: 'wrap',
          alignItems: 'center',
        }}
      >
        <span>
          <strong>Messages:</strong> {c.messages?.length ?? 0}
        </span>
        <span>
          <strong>Created:</strong> {created}
        </span>
        {c.scheduled_at && (
          <span>
            <strong>Scheduled:</strong> {scheduled}
          </span>
        )}
        {c.id != null && <span style={{ color: '#888' }}># {c.id}</span>}

        <span style={{ marginLeft: 'auto', display: 'inline-flex', gap: 6 }}>
          <select
            value={c.status ?? 'DRAFT'}
            onChange={(e) => onStatus(c, e.target.value)}
            style={{
              padding: '6px 10px',
              border: '1px solid #ddd',
              borderRadius: 8,
              background: '#fff',
            }}
            title="Change status"
          >
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>

          <button
            onClick={() => onEdit(c)}
            style={{
              background: '#fff',
              color: '#111',
              border: '1px solid #ddd',
              padding: '6px 10px',
              borderRadius: 8,
              cursor: 'pointer',
            }}
            title="Edit"
          >
            Edit
          </button>
          <button
            onClick={() => onDelete(c)}
            style={{
              background: '#fff',
              color: '#b91c1c',
              border: '1px solid #f0bcbc',
              padding: '6px 10px',
              borderRadius: 8,
              cursor: 'pointer',
            }}
            title="Delete"
          >
            Delete
          </button>
        </span>
      </div>
    </Card>
  )
}

function ErrorBanner({ message, onRetry }) {
  return (
    <div
      style={{
        margin: '12px 20px 0',
        padding: 12,
        background: '#fde8e8',
        color: '#7a1d1d',
        border: '1px solid #f7c6c6',
        borderRadius: 8,
      }}
    >
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          gap: 12,
          alignItems: 'center',
        }}
      >
        <div>
          <strong>Error:</strong> {message}
        </div>
        {onRetry && (
          <button
            onClick={onRetry}
            style={{
              background: '#7a1d1d',
              color: '#fff',
              border: 'none',
              borderRadius: 6,
              padding: '6px 10px',
              cursor: 'pointer',
            }}
          >
            Retry
          </button>
        )}
      </div>
    </div>
  )
}

function CreateEditDialog({ open, onClose, onSubmit, busy, initial }) {
  const [title, setTitle] = useState(initial?.title ?? initial?.name ?? '')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [recipient, setRecipient] = useState(
    initial?.messages?.[0]?.recipient ?? ''
  )
  const [content, setContent] = useState(
    initial?.messages?.[0]?.content ?? ''
  )
  const isEdit = initial?.id != null

  useEffect(() => {
    setTitle(initial?.title ?? initial?.name ?? '')
    setDescription(initial?.description ?? '')
    setRecipient(initial?.messages?.[0]?.recipient ?? '')
    setContent(initial?.messages?.[0]?.content ?? '')
  }, [initial])

  if (!open) return null
  return (
    <div
      role="dialog"
      aria-modal="true"
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.35)',
        display: 'grid',
        placeItems: 'center',
        padding: 16,
      }}
      onClick={onClose}
    >
      <div
        style={{
          width: 'min(680px, 92vw)',
          background: '#fff',
          borderRadius: 14,
          padding: 20,
          boxShadow: '0 10px 30px rgba(0,0,0,0.25)',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <h2 style={{ marginTop: 0 }}>
          {isEdit ? 'Edit campaign' : 'Create campaign'}
        </h2>

        <div style={{ display: 'grid', gap: 10 }}>
          <label style={{ display: 'grid', gap: 6 }}>
            <span style={{ fontSize: 13, color: '#555' }}>Title *</span>
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="October promo"
              style={{
                padding: '10px 12px',
                border: '1px solid #ddd',
                borderRadius: 8,
              }}
            />
          </label>

          <label style={{ display: 'grid', gap: 6 }}>
            <span style={{ fontSize: 13, color: '#555' }}>Description</span>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Optional details"
              rows={3}
              style={{
                padding: '10px 12px',
                border: '1px solid #ddd',
                borderRadius: 8,
              }}
            />
          </label>

          <div style={{ display: 'grid', gap: 10, gridTemplateColumns: '1fr' }}>
            <label style={{ display: 'grid', gap: 6 }}>
              <span style={{ fontSize: 13, color: '#555' }}>Recipient</span>
              <input
                value={recipient}
                onChange={(e) => setRecipient(e.target.value)}
                placeholder="test@example.com"
                style={{
                  padding: '10px 12px',
                  border: '1px solid #ddd',
                  borderRadius: 8,
                }}
              />
            </label>
            <label style={{ display: 'grid', gap: 6 }}>
              <span style={{ fontSize: 13, color: '#555' }}>Message</span>
              <textarea
                value={content}
                onChange={(e) => setContent(e.target.value)}
                placeholder="Hello from SmartSell"
                rows={3}
                style={{
                  padding: '10px 12px',
                  border: '1px solid #ddd',
                  borderRadius: 8,
                }}
              />
            </label>
          </div>
        </div>

        <div
          style={{
            display: 'flex',
            justifyContent: 'flex-end',
            gap: 10,
            marginTop: 16,
          }}
        >
          <button
            onClick={onClose}
            disabled={busy}
            style={{
              background: '#fff',
              border: '1px solid #ddd',
              borderRadius: 8,
              padding: '10px 12px',
              cursor: 'pointer',
            }}
          >
            Cancel
          </button>
          <button
            onClick={() =>
              onSubmit({
                title: title.trim() || `Untitled ${Date.now()}`,
                name: title.trim() || `Untitled ${Date.now()}`, // для бэков, ожидающих name
                description: description.trim() || null,
                messages:
                  recipient || content
                    ? [
                        {
                          recipient: recipient || 'test@example.com',
                          content: content || 'Hello!',
                          status: 'pending',
                        },
                      ]
                    : [],
              })
            }
            disabled={busy}
            style={{
              background: '#111827',
              color: '#fff',
              border: 'none',
              borderRadius: 8,
              padding: '10px 14px',
              cursor: 'pointer',
              opacity: busy ? 0.8 : 1,
            }}
          >
            {busy ? (isEdit ? 'Saving…' : 'Creating…') : isEdit ? 'Save' : 'Create'}
          </button>
        </div>
      </div>
    </div>
  )
}

function Pagination({ page, total, pageSize, onPage }) {
  const pages = Math.max(1, Math.ceil(total / pageSize))
  return (
    <div
      style={{
        display: 'flex',
        gap: 8,
        alignItems: 'center',
        justifyContent: 'flex-end',
        padding: '12px 20px',
      }}
    >
      <span style={{ color: '#666', fontSize: 12 }}>
        Page {page + 1} / {pages}
      </span>
      <button
        onClick={() => onPage(Math.max(0, page - 1))}
        disabled={page <= 0}
        style={{
          background: '#fff',
          border: '1px solid #ddd',
          padding: '6px 10px',
          borderRadius: 8,
          cursor: page <= 0 ? 'not-allowed' : 'pointer',
          opacity: page <= 0 ? 0.6 : 1,
        }}
      >
        Prev
      </button>
      <button
        onClick={() => onPage(page + 1)}
        disabled={(page + 1) * pageSize >= total}
        style={{
          background: '#fff',
          border: '1px solid #ddd',
          padding: '6px 10px',
          borderRadius: 8,
          cursor:
            (page + 1) * pageSize >= total ? 'not-allowed' : 'pointer',
          opacity: (page + 1) * pageSize >= total ? 0.6 : 1,
        }}
      >
        Next
      </button>
    </div>
  )
}

export default function App() {
  const apiUrl = apiClient.baseURL // фактическая база из клиента

  // Health
  const [health, setHealth] = useState(null)

  // Data
  const [items, setItems] = useState([])
  const [total, setTotal] = useState(0)

  // UI state
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const [dialogOpen, setDialogOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [editItem, setEditItem] = useState(null)

  // Controls
  const [query, setQuery] = useState('')
  const debouncedQuery = useDebouncedValue(query, 250)
  const [page, setPage] = useState(0)
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE)

  const mounted = useRef(true)

  const loadHealth = useCallback(async () => {
    try {
      const h = await apiClient.healthCheck()
      setHealth(h)
    } catch (e) {
      // health может падать отдельно — не блокируем UI
      setHealth({ status: 'unknown' })
    }
  }, [])

  const loadList = useCallback(
    async (opts) => {
      const { page: p = page, pageSize: ps = pageSize } = opts || {}
      setError(null)
      setLoading(true)
      try {
        const skip = p * ps
        const res = await apiClient.getCampaigns(skip, ps)
        // Поддержка 2 форматов: массив или { items, total }
        const arr = Array.isArray(res) ? res : res?.items ?? []
        const tot =
          typeof res?.total === 'number'
            ? res.total
            : Array.isArray(res)
            ? skip + arr.length + (arr.length === ps ? ps : 0) // грубая оценка
            : arr.length
        if (!mounted.current) return
        setItems(arr)
        setTotal(tot)
      } catch (e) {
        if (!mounted.current) return
        setError(e?.message || 'Failed to load data')
      } finally {
        if (mounted.current) setLoading(false)
      }
    },
    [page, pageSize]
  )

  const reloadAll = useCallback(async () => {
    await Promise.all([loadHealth(), loadList({})])
  }, [loadHealth, loadList])

  useEffect(() => {
    mounted.current = true
    reloadAll()
    // авто-обновление health раз в 30с
    const t = setInterval(loadHealth, 30000)
    return () => {
      mounted.current = false
      clearInterval(t)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Перезагрузка списка при изменении пагинации
  useEffect(() => {
    loadList({ page, pageSize })
  }, [page, pageSize, loadList])

  // Клиентский поиск по названию
  const filteredItems = useMemo(() => {
    if (!debouncedQuery.trim()) return items
    const q = debouncedQuery.trim().toLowerCase()
    return items.filter((c) => toDisplayName(c).toLowerCase().includes(q))
  }, [items, debouncedQuery])

  // ---------- Actions ----------
  async function handleCreate(payload) {
    setSubmitting(true)
    try {
      // оптимистичное добавление в текущий список (локально)
      const tempId = `tmp-${Date.now()}`
      const optimistic = {
        id: tempId,
        status: 'DRAFT',
        created_at: toIso(new Date()),
        ...payload,
      }
      setItems((prev) => [optimistic, ...prev])

      await apiClient.createCampaign(payload)
      await loadList({ page: 0, pageSize }) // к первой странице после создания
      setDialogOpen(false)
      setEditItem(null)
      setError(null)
      setPage(0)
    } catch (e) {
      setError(e?.message || 'Failed to create campaign')
    } finally {
      setSubmitting(false)
    }
  }

  async function handleSave(payload) {
    if (!editItem?.id) {
      return handleCreate(payload)
    }
    setSubmitting(true)
    try {
      await apiClient.updateCampaign(editItem.id, payload)
      await loadList({ page, pageSize })
      setDialogOpen(false)
      setEditItem(null)
      setError(null)
    } catch (e) {
      setError(e?.message || 'Failed to save campaign')
    } finally {
      setSubmitting(false)
    }
  }

  async function handleDelete(c) {
    if (!c?.id) return
    const ok = confirm(`Delete campaign "${toDisplayName(c)}"?`)
    if (!ok) return
    try {
      await apiClient.deleteCampaign(c.id)
      await loadList({ page, pageSize })
    } catch (e) {
      setError(e?.message || 'Failed to delete campaign')
    }
  }

  async function handleStatus(c, newStatus) {
    if (!c?.id) return
    const status = (newStatus || '').toUpperCase()
    if (!STATUSES.includes(status)) return
    try {
      await apiClient.updateCampaign(c.id, { status })
      // локальное обновление без полного refetch для отзывчивости
      setItems((prev) =>
        prev.map((x) => (x.id === c.id ? { ...x, status } : x))
      )
    } catch (e) {
      setError(e?.message || 'Failed to update status')
    }
  }

  return (
    <div
      style={{
        minHeight: '100vh',
        background: '#fafafa',
        color: '#111',
        fontFamily: 'Inter, system-ui, Arial, sans-serif',
      }}
    >
      <Header />
      <Status health={health} apiUrl={apiUrl} />

      {error && <ErrorBanner message={error} onRetry={() => loadList({ page, pageSize })} />}

      <Toolbar
        onCreateClick={() => {
          setEditItem(null)
          setDialogOpen(true)
        }}
        refreshing={loading}
        onRefresh={() => loadList({ page, pageSize })}
        q={query}
        onQuery={(v) => {
          setQuery(v)
          // поиск не меняет серверную пагинацию, это чисто клиентская фильтрация
        }}
        pageSize={pageSize}
        onChangePageSize={(n) => {
          setPageSize(n)
          setPage(0)
        }}
      />

      <main style={{ padding: '0 20px 28px' }}>
        {loading ? (
          <Card muted>
            <p style={{ margin: 0, opacity: 0.7 }}>Loading…</p>
          </Card>
        ) : filteredItems.length === 0 ? (
          <Card>
            {items.length === 0 && !debouncedQuery ? (
              <>
                <p style={{ margin: '0 0 12px 0' }}>No campaigns yet.</p>
                <button
                  onClick={() => {
                    setEditItem(null)
                    setDialogOpen(true)
                  }}
                  style={{
                    background: '#111827',
                    color: '#fff',
                    border: 'none',
                    padding: '8px 12px',
                    borderRadius: 8,
                    cursor: 'pointer',
                  }}
                >
                  Create your first campaign
                </button>
              </>
            ) : (
              <p style={{ margin: 0, opacity: 0.7 }}>
                No results for “{debouncedQuery}”
              </p>
            )}
          </Card>
        ) : (
          <div style={{ display: 'grid', gap: 12 }}>
            {filteredItems.map((c) => (
              <CampaignCard
                key={c.id ?? `${toDisplayName(c)}-${Math.random()}`}
                c={c}
                onEdit={(x) => {
                  setEditItem(x)
                  setDialogOpen(true)
                }}
                onDelete={handleDelete}
                onStatus={handleStatus}
              />
            ))}
          </div>
        )}
      </main>

      {/* Пагинация по серверу (skip/limit).
          total: если бэк отдаёт total — берём точное значение.
          если нет — будет приблизительный расчёт и кнопка Next может отключаться неточно. */}
      <Pagination
        page={page}
        total={total}
        pageSize={pageSize}
        onPage={(p) => setPage(Math.max(0, p))}
      />

      <CreateEditDialog
        open={dialogOpen}
        busy={submitting}
        initial={editItem}
        onClose={() => {
          if (!submitting) {
            setDialogOpen(false)
            setEditItem(null)
          }
        }}
        onSubmit={(payload) =>
          editItem ? handleSave(payload) : handleCreate(payload)
        }
      />
    </div>
  )
}
