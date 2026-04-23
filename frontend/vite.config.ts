import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  // Load from parent .env files (local dev)
  const env = loadEnv(mode, '../', '')

  // Resolve each variable: .env file values → process.env (Cloudflare Pages) → fallback
  const supabaseUrl =
    env.SUPABASE_URL ||
    process.env.SUPABASE_URL ||
    env.VITE_SUPABASE_URL ||
    process.env.VITE_SUPABASE_URL ||

    ''
  const supabaseAnonKey =
    env.SUPABASE_ANON_KEY ||
    process.env.SUPABASE_ANON_KEY ||
    env.VITE_SUPABASE_ANON_KEY ||
    process.env.VITE_SUPABASE_ANON_KEY ||
    ''

  const apiBaseUrl = env.VITE_API_BASE_URL || process.env.VITE_API_BASE_URL
    || 'http://localhost:8000'

  return {
    plugins: [react()],
    envDir: '../',
    define: {
      'import.meta.env.VITE_SUPABASE_URL': JSON.stringify(supabaseUrl),
      'import.meta.env.VITE_SUPABASE_ANON_KEY': JSON.stringify(supabaseAnonKey),
      'import.meta.env.VITE_API_BASE_URL': JSON.stringify(apiBaseUrl)
    }
  }
})
