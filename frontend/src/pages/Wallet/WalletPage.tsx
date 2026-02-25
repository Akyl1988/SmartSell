import { useEffect, useState } from 'react'
import {
  getWalletBalance,
  listWalletAccounts,
  BalanceOut,
  WalletAccountOut,
} from '../../api/wallet'

export default function WalletPage() {
  const [accounts, setAccounts] = useState<WalletAccountOut[]>([])
  const [balance, setBalance] = useState<BalanceOut | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    listWalletAccounts({ page: 1, size: 20 })
      .then((res) => setAccounts(res.items))
      .catch(() => setError('Failed to load wallet accounts.'))
  }, [])

  async function loadBalance(accountId: number) {
    const data = await getWalletBalance(accountId)
    setBalance(data)
  }

  return (
    <section>
      <h1>Wallet</h1>
      {error && <p style={{ color: '#b91c1c' }}>{error}</p>}
      {!error && accounts.length === 0 && <p>No accounts yet.</p>}
      <ul>
        {accounts.map((account) => (
          <li key={account.id}>
            {account.currency} — {account.balance}
            <button onClick={() => loadBalance(account.id)} style={{ marginLeft: 8 }}>
              Balance
            </button>
          </li>
        ))}
      </ul>
      {balance && (
        <p>
          Balance: {balance.balance} {balance.currency}
        </p>
      )}
    </section>
  )
}
