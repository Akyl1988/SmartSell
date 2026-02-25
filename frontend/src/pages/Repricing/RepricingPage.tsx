import { useEffect, useState } from 'react'
import {
  listRepricingRules,
  listRepricingRuns,
  RepricingRuleResponse,
  RepricingRunResponse,
  runRepricing,
} from '../../api/pricing'
import { useFeatureGate } from '../../hooks/useFeatureGate'

export default function RepricingPage() {
  const { hasRepricing, paymentRequired } = useFeatureGate()
  const [rules, setRules] = useState<RepricingRuleResponse[]>([])
  const [runs, setRuns] = useState<RepricingRunResponse[]>([])
  const [error, setError] = useState<string | null>(null)
  const [blocked, setBlocked] = useState(false)

  useEffect(() => {
    if (!hasRepricing || paymentRequired) return
    Promise.all([
      listRepricingRules({ page: 1, per_page: 20 }),
      listRepricingRuns({ page: 1, per_page: 20 }),
    ])
      .then(([rulesRes, runsRes]) => {
        setRules(rulesRes.items)
        setRuns(runsRes.items)
      })
      .catch((err) => {
        if (err?.response?.status === 402) {
          setBlocked(true)
          return
        }
        setError('Failed to load repricing data.')
      })
  }, [hasRepricing, paymentRequired])

  if (!hasRepricing || paymentRequired || blocked) {
    return (
      <section>
        <h1>Repricing</h1>
        <p>Upgrade to Pro to use this feature.</p>
      </section>
    )
  }

  async function onRun() {
    await runRepricing(false)
    const refreshed = await listRepricingRuns({ page: 1, per_page: 20 })
    setRuns(refreshed.items)
  }

  return (
    <section>
      <h1>Repricing</h1>
      {error && <p style={{ color: '#b91c1c' }}>{error}</p>}
      <button onClick={onRun}>Run repricing</button>
      <div style={{ marginTop: 12 }}>Rules: {rules.length}</div>
      <div>Runs: {runs.length}</div>
    </section>
  )
}
