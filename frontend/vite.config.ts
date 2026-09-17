import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev: forward /api/* to the FastAPI api-server (override with VITE_API_TARGET).
// The backend strips the /api prefix in-process, so the same client code works
// when uvicorn serves frontend/dist directly.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5175,
    host: '127.0.0.1',
    proxy: {
      '/api': {
        target: process.env.VITE_API_TARGET ?? 'http://127.0.0.1:8013',
        changeOrigin: true,
      },
    },
  },
  build: { target: 'es2022', sourcemap: true },
})
