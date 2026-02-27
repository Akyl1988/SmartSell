import Card from '../../components/ui/Card'
import EmptyState from '../../components/ui/EmptyState'
import ErrorState from '../../components/ui/ErrorState'
import Loader from '../../components/ui/Loader'
import { useAuth } from '../../hooks/useAuth'
import pageStyles from '../../styles/page.module.css'

export default function SettingsPage() {
  const { profile, loading, error } = useAuth()

  return (
    <section className={pageStyles.page}>
      <div className={pageStyles.pageHeader}>
        <div>
          <h1 className={pageStyles.pageTitle}>Settings</h1>
          <p className={pageStyles.pageDescription}>Manage your account profile and preferences.</p>
        </div>
      </div>

      {loading && <Loader label="Loading profile..." />}
      {error && <ErrorState message={error} />}
      {!loading && !error && !profile && (
        <EmptyState title="No profile" description="Profile details are not available." />
      )}

      {!loading && !error && profile && (
        <Card title="Profile">
          <div className={pageStyles.stack}>
            <div>ID: {profile.id ?? '—'}</div>
            <div>Name: {profile.full_name ?? '—'}</div>
            <div>Email: {profile.email ?? '—'}</div>
            <div>Phone: {profile.phone ?? '—'}</div>
            <div>Company: {profile.company_name ?? '—'}</div>
            <div>Role: {profile.role ?? '—'}</div>
          </div>
        </Card>
      )}
    </section>
  )
}