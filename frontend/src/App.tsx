import { useEffect, useMemo, useState } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import { logout, me, MeResponse } from './api/auth'
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

const navLinkStyle: React.CSSProperties = {
  display: 'block',
  padding: '8px 10px',
  borderRadius: 6,
  textDecoration: 'none',
  color: '#0f172a',
}

const navLinkActiveStyle: React.CSSProperties = {
  background: '#e2e8f0',
  fontWeight: 600,
}

const navSectionTitleStyle: React.CSSProperties = {
  fontSize: 12,
  fontWeight: 600,
  textTransform: 'uppercase',
  letterSpacing: '0.04em',
  color: '#64748b',
  marginTop: 12,
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
  const navigate = useNavigate()
  const isAuthed = Boolean(localStorage.getItem('access_token'))

  useEffect(() => {
    if (!isAuthed) {
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
  }, [isAuthed])

  useEffect(() => {
    const onUnauthorized = () => {
      localStorage.removeItem('access_token')
      localStorage.removeItem('refresh_token')
      navigate('/auth/login', { replace: true })
    }

    window.addEventListener('auth:unauthorized', onUnauthorized)
    return () => window.removeEventListener('auth:unauthorized', onUnauthorized)
  }, [navigate])

  const companyLabel = useMemo(() => {
    if (!profile) return 'Guest'
    return profile.company_name || profile.full_name || profile.phone || 'Account'
  }, [profile])

  const isPlatformAdmin = useMemo(() => {
    const role = profile?.role ?? ''
    return role === 'platform_admin' || profile?.is_superuser === true
  }, [profile])

  async function handleLogout() {
    const refreshToken = localStorage.getItem('refresh_token')
    try {
      await logout(refreshToken ? { refresh_token: refreshToken } : null)
    } catch {
      // Ignore logout errors; tokens will be cleared locally.
    } finally {
      localStorage.removeItem('access_token')
      localStorage.removeItem('refresh_token')
      navigate('/auth/login', { replace: true })
    }
  }

  return (
    <div style={appShellStyle}>
      <header style={headerStyle}>
        <strong>SmartSell</strong>
        {isAuthed && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <span style={{ fontSize: 12, color: '#6b7280' }}>
              {companyLabel} · {plan ?? 'Free'}
            </span>
            <button type="button" onClick={handleLogout}>
              Logout
            </button>
          </div>
        )}
      </header>
      <div style={bodyStyle}>
        {isAuthed && (
          <aside style={sidebarStyle}>
            <ul style={navListStyle}>
              <li>
                <NavLink
                  to="/dashboard"
                  style={({ isActive }) => ({
                    ...navLinkStyle,
                    ...(isActive ? navLinkActiveStyle : null),
                  })}
                >
                  Dashboard
                </NavLink>
              </li>
              <li>
                <NavLink
                  to="/products"
                  style={({ isActive }) => ({
                    ...navLinkStyle,
                    ...(isActive ? navLinkActiveStyle : null),
                  })}
                >
                  Products
                </NavLink>
              </li>
              {hasPreorders && (
                <li>
                  <NavLink
                    to="/preorders"
                    style={({ isActive }) => ({
                      ...navLinkStyle,
                      ...(isActive ? navLinkActiveStyle : null),
                    })}
                  >
                    Preorders
                  </NavLink>
                </li>
              )}
              {hasRepricing && (
                <li>
                  <NavLink
                    to="/repricing"
                    style={({ isActive }) => ({
                      ...navLinkStyle,
                      ...(isActive ? navLinkActiveStyle : null),
                    })}
                  >
                    Repricing
                  </NavLink>
                </li>
              )}
              <li>
                <NavLink
                  to="/wallet"
                  style={({ isActive }) => ({
                    ...navLinkStyle,
                    ...(isActive ? navLinkActiveStyle : null),
                  })}
                >
                  Wallet
                </NavLink>
              </li>
              <li>
                <NavLink
                  to="/subscriptions"
                  style={({ isActive }) => ({
                    ...navLinkStyle,
                    ...(isActive ? navLinkActiveStyle : null),
                  })}
                >
                  Subscriptions
                </NavLink>
              </li>
              <li>
                <NavLink
                  to="/reports"
                  style={({ isActive }) => ({
                    ...navLinkStyle,
                    ...(isActive ? navLinkActiveStyle : null),
                  })}
                >
                  Reports
                </NavLink>
              </li>
              <li>
                <NavLink
                  to="/settings"
                  style={({ isActive }) => ({
                    ...navLinkStyle,
                    ...(isActive ? navLinkActiveStyle : null),
                  })}
                >
                  Settings
                </NavLink>
              </li>
              {isPlatformAdmin && (
                <li style={navSectionTitleStyle}>Управление платформой</li>
              )}
              {isPlatformAdmin && (
                <li>
                  <NavLink
                    to="/owner"
                    style={({ isActive }) => ({
                      ...navLinkStyle,
                      ...(isActive ? navLinkActiveStyle : null),
                    })}
                  >
                    Обзор платформы
                  </NavLink>
                </li>
              )}
              {isPlatformAdmin && (
                <li>
                  <NavLink
                    to="/owner/companies"
                    style={({ isActive }) => ({
                      ...navLinkStyle,
                      ...(isActive ? navLinkActiveStyle : null),
                    })}
                  >
                    Магазины
                  </NavLink>
                </li>
              )}
              {isPlatformAdmin && (
                <li>
                  <NavLink
                    to="/owner/subscriptions"
                    style={({ isActive }) => ({
                      ...navLinkStyle,
                      ...(isActive ? navLinkActiveStyle : null),
                    })}
                  >
                    Подписки
                  </NavLink>
                </li>
              )}
              {isPlatformAdmin && (
                <li>
                  <NavLink
                    to="/owner/ops"
                    style={({ isActive }) => ({
                      ...navLinkStyle,
                      ...(isActive ? navLinkActiveStyle : null),
                    })}
                  >
                    Операции
                  </NavLink>
                </li>
              )}
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
