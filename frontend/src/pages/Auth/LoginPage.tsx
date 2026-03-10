import { FormEvent, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { getHttpErrorInfo } from '../../api/client'
import Button from '../../components/ui/Button'
import Card from '../../components/ui/Card'
import ErrorState from '../../components/ui/ErrorState'
import { useAuth } from '../../hooks/useAuth'
import formStyles from '../../styles/forms.module.css'
import pageStyles from '../../styles/page.module.css'

export default function LoginPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const { login } = useAuth()
  const [identifier, setIdentifier] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const params = new URLSearchParams(location.search)
  const sessionExpired = params.get('reason') === 'session_expired'

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setLoading(true)
    setError(null)

    try {
      await login({
        identifier,
        password: password || null,
      })
      navigate('/dashboard', { replace: true })
    } catch (err) {
      const info = getHttpErrorInfo(err)
      setError(info.message || 'Login failed. Check your credentials and try again.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <section className={pageStyles.authShell}>
      <Card className={pageStyles.authCard} title="Welcome back" description="Sign in to manage your store.">
        <form onSubmit={onSubmit} className={formStyles.formGrid}>
          <div className={formStyles.formRow}>
            <label className={formStyles.label}>Identifier</label>
            <input
              className={formStyles.input}
              value={identifier}
              onChange={(event) => setIdentifier(event.target.value)}
              placeholder="Phone or email"
              autoComplete="username"
            />
          </div>
          <div className={formStyles.formRow}>
            <label className={formStyles.label}>Password</label>
            <input
              className={formStyles.input}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="Password"
              type="password"
              autoComplete="current-password"
            />
          </div>
          {sessionExpired && !error && <ErrorState message="Session expired. Please sign in again." />}
          {error && <ErrorState message={error} />}
          <Button type="submit" disabled={loading}>
            {loading ? 'Signing in...' : 'Sign in'}
          </Button>
        </form>
      </Card>
    </section>
  )
}