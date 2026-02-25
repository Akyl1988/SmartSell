import { useEffect, useState } from 'react'
import {
  cancelPreorder,
  confirmPreorder,
  fulfillPreorder,
  listPreorders,
  PreorderOut,
} from '../../api/preorders'
import { useFeatureGate } from '../../hooks/useFeatureGate'

export default function PreordersPage() {
  const { hasPreorders, paymentRequired } = useFeatureGate()
  const [items, setItems] = useState<PreorderOut[]>([])
  const [error, setError] = useState<string | null>(null)
  const [blocked, setBlocked] = useState(false)

  useEffect(() => {
    if (!hasPreorders || paymentRequired) return
    listPreorders({ page: 1, per_page: 20 })
      .then((res) => setItems(res.items))
      .catch((err) => {
        if (err?.response?.status === 402) {
          setBlocked(true)
          return
        }
        setError('Failed to load preorders.')
      })
  }, [hasPreorders, paymentRequired])

  if (!hasPreorders || paymentRequired || blocked) {
    return (
      <section>
        <h1>Preorders</h1>
        <p>Upgrade to Pro to use this feature.</p>
      </section>
    )
  }

  async function onConfirm(id: number) {
    const updated = await confirmPreorder(id)
    setItems((prev) => prev.map((p) => (p.id === id ? updated : p)))
  }

  async function onCancel(id: number) {
    const updated = await cancelPreorder(id)
    setItems((prev) => prev.map((p) => (p.id === id ? updated : p)))
  }

  async function onFulfill(id: number) {
    const updated = await fulfillPreorder(id)
    setItems((prev) => prev.map((p) => (p.id === id ? updated : p)))
  }

  return (
    <section>
      <h1>Preorders</h1>
      {error && <p style={{ color: '#b91c1c' }}>{error}</p>}
      {!error && items.length === 0 && <p>No preorders yet.</p>}
      <ul style={{ display: 'grid', gap: 8 }}>
        {items.map((preorder) => (
          <li key={preorder.id}>
            #{preorder.id} — {preorder.status}
            <div style={{ display: 'flex', gap: 8, marginTop: 6 }}>
              <button onClick={() => onConfirm(preorder.id)}>Confirm</button>
              <button onClick={() => onCancel(preorder.id)}>Cancel</button>
              <button onClick={() => onFulfill(preorder.id)}>Fulfill</button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  )
}
