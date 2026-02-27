import { useEffect, useMemo, useState } from 'react'
import { getHttpErrorInfo } from '../../api/client'
import { downloadWalletTransactionsReport } from '../../api/reports'
import { listWalletAccounts, WalletAccountOut } from '../../api/wallet'
import Button from '../../components/ui/Button'
import Card from '../../components/ui/Card'
import EmptyState from '../../components/ui/EmptyState'
import ErrorState from '../../components/ui/ErrorState'
import Loader from '../../components/ui/Loader'
import { useToast } from '../../components/ui/Toast'
import pageStyles from '../../styles/page.module.css'

export default function WalletPage() {
  const { push } = useToast()
  const [accounts, setAccounts] = useState<WalletAccountOut[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  useEffect(() => {
    setLoading(true)
    setError(null)
    listWalletAccounts({ page: 1, size: 20 })
      .then((res) => setAccounts(res.items))
      .catch((err) => {
        const info = getHttpErrorInfo(err)
        const statusPart = info.status ? ` (status ${info.status})` : ''
        setError(`Failed to load wallet accounts${statusPart}: ${info.message}`)
      })
      .finally(() => {
        setLoading(false)
      })
  }, [])

  const primaryAccount = useMemo(() => accounts[0] ?? null, [accounts])

  async function downloadTransactions() {
    try {
      setActionError(null)
      const blob = await downloadWalletTransactionsReport({ limit: 500 })
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = 'wallet_transactions.csv'
      anchor.click()
      URL.revokeObjectURL(url)
      push('Wallet transactions report downloaded.', 'success')
    } catch (err) {
      const info = getHttpErrorInfo(err)
      const statusPart = info.status ? ` (status ${info.status})` : ''
      setActionError(`Failed to download wallet transactions${statusPart}: ${info.message}`)
      push('Failed to download wallet transactions.', 'danger')
    }
  }

  return (
    <section className={pageStyles.page}>
      <div className={pageStyles.pageHeader}>
        <div>
          <h1 className={pageStyles.pageTitle}>Wallet</h1>
          <p className={pageStyles.pageDescription}>Track balances and export wallet activity.</p>
        </div>
      </div>

      <Card>
        {loading && <Loader label="Loading wallet..." />}
        {error && <ErrorState message={error} />}
        {!loading && !error && !primaryAccount && (
          <EmptyState title="No accounts" description="No wallet accounts are configured yet." />
        )}

        {!loading && !error && primaryAccount && (
          <div className={pageStyles.stack}>
            <div>
              Balance: {primaryAccount.balance} {primaryAccount.currency}
            </div>
            {accounts.length > 1 && <div>Accounts: {accounts.length}</div>}
            <div className={pageStyles.inline}>
              <Button variant="ghost" onClick={downloadTransactions}>
                Download transactions CSV
              </Button>
            </div>
            {actionError && <ErrorState message={actionError} />}
          </div>
        )}
      </Card>
    </section>
  )
}