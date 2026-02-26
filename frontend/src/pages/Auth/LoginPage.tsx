import { type FormEvent, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getHttpErrorInfo } from '../../api/client'
import { login, requestOtp } from '../../api/auth'

/** Normalize phone to E.164-ish for display: keep digits + leading + */
function normalizePhone(raw: string): string {
  const digits = raw.replace(/\D/g, '')
  if (!digits) return ''
  // Kazakhstan numbers: if starts with 8, replace with 7
  const normalized = digits.startsWith('8') ? '7' + digits.slice(1) : digits
  return '+' + normalized
}

const OTP_TTL_SECONDS = 300
const OTP_RESEND_COOLDOWN = 60
const OTP_CODE_LENGTH = 6

export default function LoginPage() {
  const navigate = useNavigate()

  // Step 1: phone input; Step 2: OTP code input
  const [step, setStep] = useState<'phone' | 'otp'>('phone')
  const [phone, setPhone] = useState('')
  const [otpCode, setOtpCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  // Countdown for OTP TTL and resend cooldown
  const [ttl, setTtl] = useState(OTP_TTL_SECONDS)
  const [resendCooldown, setResendCooldown] = useState(0)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current)
    }
  }, [])

  function startTimers() {
    setTtl(OTP_TTL_SECONDS)
    setResendCooldown(OTP_RESEND_COOLDOWN)
    if (timerRef.current) clearInterval(timerRef.current)
    timerRef.current = setInterval(() => {
      setTtl((t) => Math.max(0, t - 1))
      setResendCooldown((c) => Math.max(0, c - 1))
    }, 1000)
  }

  function formatSeconds(s: number): string {
    const m = Math.floor(s / 60)
    const sec = s % 60
    return `${m}:${String(sec).padStart(2, '0')}`
  }

  async function onSendOtp(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    setLoading(true)
    const normalizedPhone = normalizePhone(phone)
    try {
      await requestOtp({ phone: normalizedPhone, purpose: 'login' })
      setStep('otp')
      startTimers()
    } catch (err) {
      const info = getHttpErrorInfo(err)
      setError(info.message || 'Failed to send OTP. Try again.')
    } finally {
      setLoading(false)
    }
  }

  async function onVerifyOtp(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    setLoading(true)
    const normalizedPhone = normalizePhone(phone)
    try {
      const tokens = await login({ identifier: normalizedPhone, otp_code: otpCode })
      localStorage.setItem('access_token', tokens.access_token)
      localStorage.setItem('refresh_token', tokens.refresh_token)
      if (timerRef.current) clearInterval(timerRef.current)
      navigate('/dashboard', { replace: true })
    } catch (err) {
      const info = getHttpErrorInfo(err)
      setError(info.message || 'Invalid or expired code. Try again.')
    } finally {
      setLoading(false)
    }
  }

  function handleBackToPhone() {
    setStep('phone')
    setOtpCode('')
    setError(null)
    if (timerRef.current) clearInterval(timerRef.current)
  }

  async function onResend() {
    if (resendCooldown > 0 || loading) return
    setError(null)
    setLoading(true)
    const normalizedPhone = normalizePhone(phone)
    try {
      await requestOtp({ phone: normalizedPhone, purpose: 'login' })
      startTimers()
    } catch (err) {
      const info = getHttpErrorInfo(err)
      setError(info.message || 'Failed to resend OTP.')
    } finally {
      setLoading(false)
    }
  }

  const formStyle: React.CSSProperties = { display: 'grid', gap: 12, maxWidth: 360 }

  if (step === 'otp') {
    return (
      <section>
        <h1>Введите код</h1>
        <p style={{ color: '#475569', marginBottom: 8 }}>
          Код отправлен на <strong>{normalizePhone(phone)}</strong>
        </p>
        <form onSubmit={onVerifyOtp} style={formStyle}>
          <label>
            Код из SMS
            <input
              value={otpCode}
              onChange={(e) => setOtpCode(e.target.value.replace(/\D/g, '').slice(0, OTP_CODE_LENGTH))}
              placeholder={'0'.repeat(OTP_CODE_LENGTH)}
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={OTP_CODE_LENGTH}
              autoFocus
            />
          </label>
          {ttl > 0 && (
            <span style={{ fontSize: 13, color: '#64748b' }}>
              Код действует: {formatSeconds(ttl)}
            </span>
          )}
          {ttl === 0 && (
            <span style={{ fontSize: 13, color: '#b91c1c' }}>
              Код истёк. Запросите новый.
            </span>
          )}
          <button type="submit" disabled={loading || otpCode.length < OTP_CODE_LENGTH}>
            {loading ? 'Проверяем...' : 'Войти'}
          </button>
          <button
            type="button"
            onClick={onResend}
            disabled={resendCooldown > 0 || loading}
            style={{ background: 'none', border: 'none', color: '#1d4ed8', cursor: resendCooldown > 0 ? 'default' : 'pointer', padding: 0, fontSize: 14 }}
          >
            {resendCooldown > 0
              ? `Отправить повторно через ${resendCooldown} сек`
              : 'Отправить код повторно'}
          </button>
          <button
            type="button"
            onClick={() => handleBackToPhone()}
            style={{ background: 'none', border: 'none', color: '#64748b', cursor: 'pointer', padding: 0, fontSize: 14 }}
          >
            ← Изменить номер
          </button>
          {error && <span style={{ color: '#b91c1c' }}>{error}</span>}
        </form>
      </section>
    )
  }

  return (
    <section>
      <h1>Войти в SmartSell</h1>
      <form onSubmit={onSendOtp} style={formStyle}>
        <label>
          Номер телефона
          <input
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
            placeholder="+7 (700) 123-45-67"
            type="tel"
            autoComplete="tel"
            autoFocus
          />
        </label>
        <button type="submit" disabled={loading || !phone.trim()}>
          {loading ? 'Отправляем...' : 'Получить код'}
        </button>
        {error && <span style={{ color: '#b91c1c' }}>{error}</span>}
      </form>
    </section>
  )
}
