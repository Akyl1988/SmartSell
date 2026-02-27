import styles from './StatusBadge.module.css'

export type StatusTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger'

type StatusBadgeProps = {
  tone?: StatusTone
  label: string
}

export default function StatusBadge({ tone = 'neutral', label }: StatusBadgeProps) {
  return <span className={[styles.badge, styles[tone]].join(' ')}>{label}</span>
}
