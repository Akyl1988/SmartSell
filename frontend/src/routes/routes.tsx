import { Navigate, Route, Routes } from 'react-router-dom'
import AppLayout from '../components/layout/AppLayout'
import ProtectedRoute from '../components/routing/ProtectedRoute'
import RoleGuard from '../components/routing/RoleGuard'
import LoginPage from '../pages/Auth/LoginPage'
import DashboardPage from '../pages/Dashboard/DashboardPage'
import KaspiPage from '../pages/KaspiPage'
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
import pageStyles from '../styles/page.module.css'

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/dashboard" replace />} />
      <Route path="/auth/login" element={<LoginPage />} />
      <Route element={<ProtectedRoute />}>
        <Route element={<AppLayout />}>
          <Route element={<RoleGuard allowedRoles={['admin', 'manager']} redirectTo="/owner" />}>
            <Route path="/dashboard" element={<DashboardPage />} />
            <Route path="/products" element={<ProductsPage />} />
            <Route path="/preorders" element={<PreordersPage />} />
            <Route path="/repricing" element={<RepricingPage />} />
            <Route path="/wallet" element={<WalletPage />} />
            <Route path="/subscriptions" element={<SubscriptionsPage />} />
            <Route path="/reports" element={<ReportsPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/kaspi" element={<KaspiPage />} />
          </Route>
          <Route element={<RoleGuard allowedRoles={['platform_admin']} allowPlatformAdmin redirectTo="/dashboard" />}>
            <Route path="/owner" element={<OwnerDashboardPage />} />
            <Route path="/owner/companies" element={<OwnerCompaniesPage />} />
            <Route path="/owner/companies/:id" element={<OwnerCompanyDetailPage />} />
            <Route path="/owner/subscriptions" element={<OwnerSubscriptionsPage />} />
            <Route path="/owner/ops" element={<OwnerOpsPage />} />
          </Route>
        </Route>
      </Route>
      <Route path="*" element={<NotFound />} />
    </Routes>
  )
}

function NotFound() {
  return (
    <section className={pageStyles.page}>
      <div className={pageStyles.pageHeader}>
        <div>
          <h1 className={pageStyles.pageTitle}>Not found</h1>
          <p className={pageStyles.pageDescription}>The page you requested does not exist.</p>
        </div>
      </div>
    </section>
  )
}
