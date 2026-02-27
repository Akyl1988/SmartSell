import { ReactNode } from 'react'
import { Outlet } from 'react-router-dom'
import { useAuth } from '../../hooks/useAuth'
import { useFeatureGate } from '../../hooks/useFeatureGate'
import Sidebar from './Sidebar'
import Topbar from './Topbar'
import styles from './AppLayout.module.css'

type AppLayoutProps = {
  children?: ReactNode
}

export default function AppLayout({ children }: AppLayoutProps) {
  const { isAuthed, profile, logout } = useAuth()
  const { hasPreorders, hasRepricing, plan } = useFeatureGate()

  const content = children ?? <Outlet />

  return (
    <div className={styles.shell}>
      {isAuthed && (
        <Topbar
          companyLabel={profile?.company_name || profile?.full_name || profile?.phone || 'Account'}
          planLabel={plan ?? 'Free'}
          onLogout={logout}
        />
      )}
      <div className={styles.body}>
        {isAuthed && (
          <Sidebar
            hasPreorders={hasPreorders}
            hasRepricing={hasRepricing}
            isPlatformAdmin={Boolean(profile?.role === 'platform_admin' || profile?.is_superuser)}
          />
        )}
        <main className={styles.main}>{content}</main>
      </div>
    </div>
  )
}
