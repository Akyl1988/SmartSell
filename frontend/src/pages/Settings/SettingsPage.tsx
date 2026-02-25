import { useEffect, useState } from 'react'
import { me, MeResponse } from '../../api/auth'

export default function SettingsPage() {
  const [profile, setProfile] = useState<MeResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    me()
      .then(setProfile)
      .catch(() => setError('Failed to load profile.'))
  }, [])

  return (
    <section>
      <h1>Settings</h1>
      {error && <p style={{ color: '#b91c1c' }}>{error}</p>}
      {profile && <pre>{JSON.stringify(profile, null, 2)}</pre>}
    </section>
  )
}
