import styles from './Loader.module.css'

type LoaderProps = {
  label?: string
}

export default function Loader({ label = 'Loading...' }: LoaderProps) {
  return (
    <div className={styles.wrapper}>
      <span className={styles.spinner} />
      <span className={styles.label}>{label}</span>
    </div>
  )
}
