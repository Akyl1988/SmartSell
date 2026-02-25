import { type FormEvent, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { login } from '../../api/auth'

export default function LoginPage() {
  const navigate = useNavigate()
  const [identifier, setIdentifier] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setLoading(true)
    setError(null)

    try {
      const tokens = await login({
        identifier,
        password: password || null,
      })
      localStorage.setItem('access_token', tokens.access_token)
      localStorage.setItem('refresh_token', tokens.refresh_token)
      navigate('/dashboard', { replace: true })
    } catch (err) {
      setError('Login failed. Check your credentials and try again.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <section>
      <h1>Login</h1>
      <form onSubmit={onSubmit} style={{ display: 'grid', gap: 12, maxWidth: 360 }}>
        <label>
          Identifier
          <input
            value={identifier}
            onChange={(event) => setIdentifier(event.target.value)}
            placeholder="Phone or email"
          />
        </label>
        <label>
          Password
          <input
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder="Password"
            type="password"
          />
        </label>
        <button type="submit" disabled={loading}>
          {loading ? 'Signing in...' : 'Sign in'}
        </button>
        {error && <span style={{ color: '#b91c1c' }}>{error}</span>}
      </form>
    </section>
  )
}
