import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The investor app is its own build on its own origin (app.alphavantiqcapital.com),
// separate from the admin console, so nothing admin-only is ever shipped to it.
export default defineConfig({
  plugins: [react()],
  server: { port: 5174, host: true },
})
