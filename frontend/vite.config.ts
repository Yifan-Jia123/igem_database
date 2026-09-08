import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Node's `process` is only used at config-evaluation time to allow port/backend
// overrides; keep this config type-checkable without pulling in @types/node.
declare const process: { env: Record<string, string | undefined> }

export default defineConfig({
  plugins: [react()],
  server: {
    // Overridable so this codebase can run beside another stack on 5173/8000:
    //   VITE_PORT=5175 VITE_BACKEND=http://127.0.0.1:8009 npm run dev
    port: Number(process.env.VITE_PORT) || 5173,
    strictPort: false,
    proxy: {
      '/api': {
        target: process.env.VITE_BACKEND || 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
