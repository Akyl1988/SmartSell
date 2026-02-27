import Button from './Button'
import styles from './State.module.css'

type ErrorStateProps = {
  title?: string
  message: string
  onRetry?: () => void
}

export default function ErrorState({ title = 'Something went wrong', message, onRetry }: ErrorStateProps) {
  return (
    <div className={styles.state}>
      <strong>{title}</strong>
      <p>{message}</p>
      {onRetry && (
        <Button variant="ghost" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  )
}
