import { HTMLAttributes, ReactNode } from 'react'
import styles from './Table.module.css'

type TableProps = HTMLAttributes<HTMLTableElement>

type TableSectionProps = {
  children: ReactNode
  className?: string
}

export function Table({ className = '', ...rest }: TableProps) {
  return <table className={[styles.table, className].filter(Boolean).join(' ')} {...rest} />
}

export function TableHead({ children, className = '' }: TableSectionProps) {
  return <thead className={[styles.head, className].filter(Boolean).join(' ')}>{children}</thead>
}

export function TableBody({ children, className = '' }: TableSectionProps) {
  return <tbody className={className}>{children}</tbody>
}

export function TableRow({ children, className = '' }: TableSectionProps) {
  return <tr className={className}>{children}</tr>
}

export function TableHeaderCell({ children, className = '' }: TableSectionProps) {
  return <th className={[styles.headerCell, className].filter(Boolean).join(' ')}>{children}</th>
}

export function TableCell({ children, className = '' }: TableSectionProps) {
  return <td className={[styles.cell, className].filter(Boolean).join(' ')}>{children}</td>
}
