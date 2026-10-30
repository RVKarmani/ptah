import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// base './' => relative asset paths, deployable from any sub-path (Vercel/Netlify/static host)
export default defineConfig({
  base: './',
  plugins: [react()],
  build: { chunkSizeWarningLimit: 2000 },
})
