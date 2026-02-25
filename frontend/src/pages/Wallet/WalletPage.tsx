import { useEffect, useMemo, useState } from 'react'
import { getHttpErrorInfo } from '../../api/client'
import { listWalletAccounts, WalletAccountOut } from '../../api/wallet'
import { downloadWalletTransactionsReport } from '../../api/reports'

export default function WalletPage() {
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

  return (
    <section>
      <h1>Wallet</h1>
      {loading && <p>Loading wallet...</p>}
      {error && <p style={{ color: '#b91c1c' }}>{error}</p>}
      {!loading && !error && !primaryAccount && <p>No accounts yet.</p>}
      {!loading && !error && primaryAccount && (
        <div style={{ display: 'grid', gap: 8 }}>
          <div>
            Balance: {primaryAccount.balance} {primaryAccount.currency}
          </div>
          {accounts.length > 1 && <div>Accounts: {accounts.length}</div>}
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <button
              onClick={async () => {
                try {
                  setActionError(null)
                  const blob = await downloadWalletTransactionsReport({ limit: 500 })
                  const url = URL.createObjectURL(blob)
                  const anchor = document.createElement('a')
                  anchor.href = url
                  anchor.download = 'wallet_transactions.csv'
                  anchor.click()
                  URL.revokeObjectURL(url)
                } catch (err) {
                  const info = getHttpErrorInfo(err)
                  const statusPart = info.status ? ` (status ${info.status})` : ''
                  setActionError(`Failed to download wallet transactions${statusPart}: ${info.message}`)
                }
              }}
            >
              Download transactions CSV
            </button>
          </div>
        </div>
      )}
      {actionError && <p style={{ color: '#b91c1c', marginTop: 12 }}>{actionError}</p>}
    </section>
  )
}
