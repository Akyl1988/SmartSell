import { useEffect, useState } from 'react'
import { listProducts, ProductResponse } from '../../api/products'

export default function ProductsPage() {
  const [items, setItems] = useState<ProductResponse[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    listProducts({ page: 1, per_page: 20 })
      .then((res) => setItems(res.items))
      .catch(() => setError('Failed to load products.'))
  }, [])

  return (
    <section>
      <h1>Products</h1>
      {error && <p style={{ color: '#b91c1c' }}>{error}</p>}
      {!error && items.length === 0 && <p>No products yet.</p>}
      <ul>
        {items.map((product) => (
          <li key={product.id}>
            {product.name} — {product.price}
          </li>
        ))}
      </ul>
    </section>
  )
}
