import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// em desenvolvimento a API roda em :8000 (uvicorn); no build, o próprio backend serve o dist/
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { '/api': 'http://127.0.0.1:8000' } },
})
