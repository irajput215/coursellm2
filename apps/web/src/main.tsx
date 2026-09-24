import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter } from 'react-router-dom'

import { App } from './App'
import { ErrorBoundary } from './components/ErrorBoundary'
import { AuthProvider } from './features/auth/AuthProvider'
import { createQueryClient } from './lib/queryClient'
import './styles/global.css'

const container = document.getElementById('root')
if (container === null) {
  throw new Error('index.html is missing the #root container.')
}

const queryClient = createQueryClient()

createRoot(container).render(
  <StrictMode>
    <ErrorBoundary title="CourseLLM failed to start">
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <AuthProvider>
            <App />
          </AuthProvider>
        </BrowserRouter>
      </QueryClientProvider>
    </ErrorBoundary>
  </StrictMode>,
)
