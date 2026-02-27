import Button from '../ui/Button'
import styles from './Topbar.module.css'

type TopbarProps = {
  companyLabel: string
  planLabel: string
  onLogout: () => void
}

export default function Topbar({ companyLabel, planLabel, onLogout }: TopbarProps) {
  return (
    <header className={styles.topbar}>
      <div className={styles.brand}>SmartSell</div>
      <div className={styles.meta}>
        <span className={styles.company}>
          {companyLabel} · {planLabel}
        </span>
        <Button variant="ghost" size="sm" onClick={onLogout}>
          Logout
        </Button>
      </div>
    </header>
  )
}
