import { useCallback, useEffect, useMemo, useState } from 'react'
import { getHttpErrorInfo } from '../api/client'
import { login as apiLogin, logout as apiLogout, me, LoginPayload, MeResponse, TokenResponse } from '../api/auth'

type AuthState = {
  currentUser: MeResponse | null
  profile: MeResponse | null
  loading: boolean
  authenticating: boolean
  error: string | null
  isAuthed: boolean
  isAuthenticated: boolean
  role: string | null
  isPlatformAdmin: boolean
  isSuperuser: boolean
  isStoreAdmin: boolean
  refreshProfile: () => Promise<void>
  login: (payload: LoginPayload) => Promise<TokenResponse>
  logout: () => Promise<void>
}

export function useAuth(): AuthState {
  const [profile, setProfile] = useState<MeResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [authenticating, setAuthenticating] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refreshProfile = useCallback(async () => {
    const token = localStorage.getItem('access_token')
    if (!token) {
      setProfile(null)
      setLoading(false)
      return
    }
    setLoading(true)
    setError(null)
    try {
      const data = await me()
      setProfile(data)
    } catch {
      setProfile(null)
      setError('Failed to load session.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refreshProfile()
  }, [refreshProfile])

  useEffect(() => {
    const onUnauthorized = () => {
      localStorage.removeItem('access_token')
      localStorage.removeItem('refresh_token')
      setProfile(null)
    }
    window.addEventListener('auth:unauthorized', onUnauthorized)
    return () => window.removeEventListener('auth:unauthorized', onUnauthorized)
  }, [])

  const login = useCallback(async (payload: LoginPayload) => {
    setAuthenticating(true)
    setError(null)
    try {
      const tokens = await apiLogin(payload)
      localStorage.setItem('access_token', tokens.access_token)
      localStorage.setItem('refresh_token', tokens.refresh_token)
      try {
        const data = await me()
        setProfile(data)
      } catch {
        setProfile(null)
      }
      return tokens
    } catch (err) {
      const info = getHttpErrorInfo(err)
      setError(info.message)
      throw err
    } finally {
      setAuthenticating(false)
    }
  }, [])

  const logout = useCallback(async () => {
    const refreshToken = localStorage.getItem('refresh_token')
    try {
      await apiLogout(refreshToken ? { refresh_token: refreshToken } : null)
    } catch {
      // ignore
    } finally {
      localStorage.removeItem('access_token')
      localStorage.removeItem('refresh_token')
      setProfile(null)
    }
  }, [])

  const role = profile?.role ?? null
  const isSuperuser = Boolean(profile?.is_superuser)
  const isPlatformAdmin = Boolean(role?.toLowerCase() === 'platform_admin' || isSuperuser)
  const isStoreAdmin = Boolean(role?.toLowerCase() === 'admin' || role?.toLowerCase() === 'manager')
  const isAuthenticated = Boolean(profile)

  return useMemo(
    () => ({
      currentUser: profile,
      profile,
      loading,
      authenticating,
      error,
      isAuthed: isAuthenticated,
      isAuthenticated,
      role,
      isPlatformAdmin,
      isSuperuser,
      isStoreAdmin,
      refreshProfile,
      login,
      logout,
    }),
    [
      profile,
      loading,
      authenticating,
      error,
      isAuthenticated,
      role,
      isPlatformAdmin,
      isSuperuser,
      isStoreAdmin,
      refreshProfile,
      login,
      logout,
    ]
  )
}
