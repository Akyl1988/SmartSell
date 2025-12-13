// vite.config.js
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')

  const useProxy = String(env.VITE_USE_PROXY || 'true') === 'true'
  const proxyTarget = env.VITE_PROXY_TARGET || 'http://127.0.0.1:8000'

  return {
    plugins: [react()],
    server: {
      port: 5173,
      strictPort: false,
      open: false,
      proxy: useProxy
        ? {
            // все вызовы на /api/* уйдут на backend
            '/api': {
              target: proxyTarget,
              changeOrigin: true,
              secure: false,
              ws: true,
              // убираем префикс /api при проксировании
              rewrite: (path) => path.replace(/^\/api/, ''),
            },
          }
        : undefined,
    },
    build: {
      sourcemap: true,
    },
  }
})
