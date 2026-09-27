import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The backend (backend/app.py) runs on port 8000; the dev server forwards to it so the
// page can use same-origin URLs.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/ws': { target: 'ws://localhost:8000', ws: true },
      '/api': { target: 'http://localhost:8000' },
    },
  },
})
