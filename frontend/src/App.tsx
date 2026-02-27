import { ToastProvider } from './components/ui/Toast'
import { AppRoutes } from './routes/routes'

export default function App() {
  return (
    <ToastProvider>
      <AppRoutes />
    </ToastProvider>
  )
}
