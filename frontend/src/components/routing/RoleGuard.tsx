import { Navigate, Outlet } from 'react-router-dom'
import { useAuth } from '../../hooks/useAuth'
import Loader from '../ui/Loader'

type RoleGuardProps = {
  allowedRoles: string[]
  allowPlatformAdmin?: boolean
  redirectTo?: string
}

export default function RoleGuard({ allowedRoles, allowPlatformAdmin = false, redirectTo = '/dashboard' }: RoleGuardProps) {
  const { loading, role, isPlatformAdmin } = useAuth()

  if (loading) {
    return <Loader label="Checking permissions..." />
  }

  const normalizedRole = (role ?? '').toLowerCase()
  const isAllowedRole = allowedRoles.map((value) => value.toLowerCase()).includes(normalizedRole)
  const canAccess = isAllowedRole || (allowPlatformAdmin && isPlatformAdmin)

  if (!canAccess) {
    return <Navigate to={redirectTo} replace />
  }

  return <Outlet />
}
