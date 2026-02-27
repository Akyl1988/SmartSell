import { Navigate, Outlet } from 'react-router-dom'
import { useAuth } from '../../hooks/useAuth'
import Loader from '../ui/Loader'

export default function ProtectedRoute() {
  const { isAuthed, loading } = useAuth()

  if (loading) {
    return <Loader label="Checking session..." />
  }

  if (!isAuthed) {
    return <Navigate to="/auth/login" replace />
  }

  return <Outlet />
}
