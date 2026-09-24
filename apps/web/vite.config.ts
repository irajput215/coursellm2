import { fileURLToPath, URL } from 'node:url'

import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The dev server proxies the API so the browser sees a single origin, which is
// also how the built assets are deployed (static files on S3/CloudFront with
// `/api` routed to the API). No Vercel, no serverless functions.
const apiTarget = process.env.VITE_DEV_API_TARGET ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': { target: apiTarget, changeOrigin: true },
      '/healthz': { target: apiTarget, changeOrigin: true },
      '/readyz': { target: apiTarget, changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    // The bundle is served as static assets; hashed filenames make the
    // CloudFront invalidation strategy trivial.
    sourcemap: false,
    chunkSizeWarningLimit: 900,
  },
})
