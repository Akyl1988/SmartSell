import { NavLink } from 'react-router-dom'
import styles from './Sidebar.module.css'

type SidebarProps = {
  hasPreorders: boolean
  hasRepricing: boolean
  isPlatformAdmin: boolean
}

export default function Sidebar({ hasPreorders, hasRepricing, isPlatformAdmin }: SidebarProps) {
  return (
    <aside className={styles.sidebar}>
      <nav>
        <ul className={styles.navList}>
          <li>
            <NavLink to="/dashboard" className={({ isActive }) => (isActive ? styles.active : styles.link)}>
              Dashboard
            </NavLink>
          </li>
          <li>
            <NavLink to="/products" className={({ isActive }) => (isActive ? styles.active : styles.link)}>
              Products
            </NavLink>
          </li>
          {hasPreorders && (
            <li>
              <NavLink to="/preorders" className={({ isActive }) => (isActive ? styles.active : styles.link)}>
                Preorders
              </NavLink>
            </li>
          )}
          {hasRepricing && (
            <li>
              <NavLink to="/repricing" className={({ isActive }) => (isActive ? styles.active : styles.link)}>
                Repricing
              </NavLink>
            </li>
          )}
          <li>
            <NavLink to="/kaspi" className={({ isActive }) => (isActive ? styles.active : styles.link)}>
              Kaspi
            </NavLink>
          </li>
          <li>
            <NavLink to="/wallet" className={({ isActive }) => (isActive ? styles.active : styles.link)}>
              Wallet
            </NavLink>
          </li>
          <li>
            <NavLink to="/subscriptions" className={({ isActive }) => (isActive ? styles.active : styles.link)}>
              Subscriptions
            </NavLink>
          </li>
          <li>
            <NavLink to="/reports" className={({ isActive }) => (isActive ? styles.active : styles.link)}>
              Reports
            </NavLink>
          </li>
          <li>
            <NavLink to="/settings" className={({ isActive }) => (isActive ? styles.active : styles.link)}>
              Settings
            </NavLink>
          </li>
        </ul>

        {isPlatformAdmin && (
          <div className={styles.section}>
            <div className={styles.sectionTitle}>Platform</div>
            <ul className={styles.navList}>
              <li>
                <NavLink to="/owner" className={({ isActive }) => (isActive ? styles.active : styles.link)}>
                  Overview
                </NavLink>
              </li>
              <li>
                <NavLink to="/owner/companies" className={({ isActive }) => (isActive ? styles.active : styles.link)}>
                  Companies
                </NavLink>
              </li>
              <li>
                <NavLink to="/owner/subscriptions" className={({ isActive }) => (isActive ? styles.active : styles.link)}>
                  Subscriptions
                </NavLink>
              </li>
              <li>
                <NavLink to="/owner/ops" className={({ isActive }) => (isActive ? styles.active : styles.link)}>
                  Ops
                </NavLink>
              </li>
            </ul>
          </div>
        )}
      </nav>
    </aside>
  )
}
