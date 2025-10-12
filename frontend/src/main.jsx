import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.jsx'

// Простая плашка, чтобы визуально понимать, что сейчас режим моков
function MockBadge() {
  if (import.meta.env.VITE_USE_MOCKS !== '1') return null
  return (
    <div
      style={{
        position: 'fixed',
        right: 8,
        bottom: 8,
        background: '#111827',
        color: '#fff',
        padding: '6px 10px',
        borderRadius: 8,
        fontSize: 12,
        zIndex: 9999,
        boxShadow: '0 2px 6px rgba(0,0,0,0.25)',
      }}
    >
      MSW: mocks ON
    </div>
  )
}

async function bootstrap() {
  const useMocks = import.meta.env.VITE_USE_MOCKS === '1'

  if (useMocks) {
    try {
      // Динамический импорт и старт воркера ДО рендера приложения
      const { worker } = await import('./mocks/browser')
      const resp = await worker.start({
        onUnhandledRequest: 'bypass', // можно 'warn' для диагностики
        serviceWorker: {
          url: '/mockServiceWorker.js', // важно: путь с корня
        },
      })
      console.log('[MSW] started:', resp)
      console.log('[MSW] controller:', navigator.serviceWorker?.controller)
    } catch (err) {
      console.error('[MSW] failed to start:', err)
    }
  } else {
    console.log('[MSW] disabled; using real backend')
  }

  const root = createRoot(document.getElementById('root'))
  root.render(
    <React.StrictMode>
      <App />
      <MockBadge />
    </React.StrictMode>
  )
}

bootstrap()
