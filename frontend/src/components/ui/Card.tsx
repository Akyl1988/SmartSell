import { ReactNode } from 'react'
import styles from './Card.module.css'

type CardProps = {
  title?: string
  description?: string
  actions?: ReactNode
  children?: ReactNode
  className?: string
}

export default function Card({ title, description, actions, children, className = '' }: CardProps) {
  return (
    <section className={[styles.card, className].filter(Boolean).join(' ')}>
      {(title || description || actions) && (
        <div className={styles.header}>
          <div>
            {title && <h3 className={styles.title}>{title}</h3>}
            {description && <p className={styles.description}>{description}</p>}
          </div>
          {actions && <div className={styles.actions}>{actions}</div>}
        </div>
      )}
      {children}
    </section>
  )
}
