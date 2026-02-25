import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { me, MeResponse } from './api/auth'
import { useFeatureGate } from './hooks/useFeatureGate'
import { AppRoutes } from './routes/routes'

const appShellStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  minHeight: '100vh',
  fontFamily: 'system-ui, -apple-system, Segoe UI, sans-serif',
  color: '#0f172a',
}

const headerStyle: React.CSSProperties = {
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
  padding: '12px 20px',
  borderBottom: '1px solid #e5e7eb',
  background: '#ffffff',
}

const bodyStyle: React.CSSProperties = {
  display: 'flex',
  flex: 1,
  minHeight: 0,
}

const sidebarStyle: React.CSSProperties = {
  width: 220,
  borderRight: '1px solid #e5e7eb',
  background: '#f8fafc',
  padding: '16px 12px',
}

const navListStyle: React.CSSProperties = {
  listStyle: 'none',
  margin: 0,
  padding: 0,
  display: 'flex',
  flexDirection: 'column',
  gap: 8,
}

const mainStyle: React.CSSProperties = {
  flex: 1,
  padding: '20px',
  background: '#ffffff',
  overflow: 'auto',
}

export default function App() {
  const { hasPreorders, hasRepricing, plan } = useFeatureGate()
  const [profile, setProfile] = useState<MeResponse | null>(null)
  const isAuthed = Boolean(localStorage.getItem('access_token'))

  useEffect(() => {
    const token = localStorage.getItem('access_token')
    if (!token) {
      setProfile(null)
      return
    }

    let mounted = true
    me()
      .then((data) => {
        if (mounted) {
          setProfile(data)
        }
      })
      .catch(() => {
        if (mounted) {
          setProfile(null)
        }
      })

    return () => {
      mounted = false
    }
  }, [])

  const companyLabel = useMemo(() => {
    if (!profile) return 'Guest'
    return profile.company_name || profile.full_name || profile.phone || 'Account'
  }, [profile])

  return (
    <div style={appShellStyle}>
      <header style={headerStyle}>
        <strong>SmartSell</strong>
        <span style={{ fontSize: 12, color: '#6b7280' }}>
          {companyLabel} · {plan ?? 'Free'}
        </span>
      </header>
      <div style={bodyStyle}>
        {isAuthed && (
          <aside style={sidebarStyle}>
            <ul style={navListStyle}>
              <li><Link to="/dashboard">Dashboard</Link></li>
              <li><Link to="/products">Products</Link></li>
              {hasPreorders && <li><Link to="/preorders">Preorders</Link></li>}
              {hasRepricing && <li><Link to="/repricing">Repricing</Link></li>}
              <li><Link to="/wallet">Wallet</Link></li>
              <li><Link to="/subscriptions">Subscriptions</Link></li>
              <li><Link to="/reports">Reports</Link></li>
              <li><Link to="/settings">Settings</Link></li>
            </ul>
          </aside>
        )}
        <main style={mainStyle}>
          <AppRoutes />
        </main>
      </div>
    </div>
  )
}
