import { Navigate, Outlet, Route, Routes } from 'react-router-dom'
import LoginPage from '../pages/Auth/LoginPage'
import DashboardPage from '../pages/Dashboard/DashboardPage'
import ProductsPage from '../pages/Products/ProductsPage'
import PreordersPage from '../pages/Preorders/PreordersPage'
import RepricingPage from '../pages/Repricing/RepricingPage'
import WalletPage from '../pages/Wallet/WalletPage'
import SubscriptionsPage from '../pages/Subscriptions/SubscriptionsPage'
import ReportsPage from '../pages/Reports/ReportsPage'
import SettingsPage from '../pages/Settings/SettingsPage'
import OwnerCompaniesPage from '../pages/Owner/OwnerCompaniesPage'
import OwnerCompanyDetailPage from '../pages/Owner/OwnerCompanyDetailPage'
import OwnerDashboardPage from '../pages/Owner/OwnerDashboardPage'
import OwnerOpsPage from '../pages/Owner/OwnerOpsPage'
import OwnerSubscriptionsPage from '../pages/Owner/OwnerSubscriptionsPage'

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/dashboard" replace />} />
      <Route path="/auth/login" element={<LoginPage />} />
      <Route element={<ProtectedRoute />}>
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/products" element={<ProductsPage />} />
        <Route path="/preorders" element={<PreordersPage />} />
        <Route path="/repricing" element={<RepricingPage />} />
        <Route path="/wallet" element={<WalletPage />} />
        <Route path="/subscriptions" element={<SubscriptionsPage />} />
        <Route path="/reports" element={<ReportsPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/owner" element={<OwnerDashboardPage />} />
        <Route path="/owner/companies" element={<OwnerCompaniesPage />} />
        <Route path="/owner/companies/:id" element={<OwnerCompanyDetailPage />} />
        <Route path="/owner/subscriptions" element={<OwnerSubscriptionsPage />} />
        <Route path="/owner/ops" element={<OwnerOpsPage />} />
      </Route>
      <Route path="*" element={<NotFound />} />
    </Routes>
  )
}

function ProtectedRoute() {
  const token = localStorage.getItem('access_token')
  if (!token) {
    return <Navigate to="/auth/login" replace />
  }
  return <Outlet />
}

function NotFound() {
  return (
    <section>
      <h1>Not found</h1>
      <p>The page you requested does not exist.</p>
    </section>
  )
}
