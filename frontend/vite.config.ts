import { defineConfig, loadEnv, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import { readdirSync, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const SITE_URL = 'https://wildlifewatcher.ai'
// The public routes in src/App.tsx. Signed-in routes need an account and are
// left out; a guide is added by merging its markdown file (see the guides README).
const PUBLIC_PATHS = ['/', '/login', '/signup', '/guides', '/faq', '/resources', '/privacy', '/terms']

/** Writes dist/sitemap.xml: the public routes plus every guide, with the guide's `updated` date. */
function sitemap(): Plugin {
  return {
    name: 'ww-sitemap',
    apply: 'build',
    generateBundle() {
      const dir = fileURLToPath(new URL('./src/content/guides/', import.meta.url))
      const guides = readdirSync(dir)
        .filter(f => f.endsWith('.md') && f !== 'README.md')
        .map(f => {
          const updated = readFileSync(dir + f, 'utf8').match(/^updated:\s*(\S+)/m)?.[1]
          return { path: '/guides/' + f.replace(/\.md$/, ''), updated }
        })
      const entries = [...PUBLIC_PATHS.map(path => ({ path, updated: undefined as string | undefined })), ...guides]
      const xml = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        ...entries.map(e => `  <url><loc>${SITE_URL}${e.path}</loc>${e.updated ? `<lastmod>${e.updated}</lastmod>` : ''}</url>`),
        '</urlset>', ''].join('\n')
      this.emitFile({ type: 'asset', fileName: 'sitemap.xml', source: xml })
    },
  }
}

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  // Load from parent .env files (local dev)
  const env = loadEnv(mode, '../', '')

  // Resolve each variable: .env file values → process.env (Cloudflare Pages) → fallback
  const supabaseUrl = env.SUPABASE_URL || process.env.SUPABASE_URL
    || env.VITE_SUPABASE_URL || process.env.VITE_SUPABASE_URL || ''
  const supabaseAnonKey = env.SUPABASE_ANON_KEY || process.env.SUPABASE_ANON_KEY
    || env.VITE_SUPABASE_ANON_KEY || process.env.VITE_SUPABASE_ANON_KEY || ''
  const apiBaseUrl = env.VITE_API_BASE_URL || process.env.VITE_API_BASE_URL
    || 'http://localhost:8000'
  // Public: the web OAuth client ID that Google's sign-in button runs under (LoginPage).
  const googleClientId = env.VITE_GOOGLE_CLIENT_ID || process.env.VITE_GOOGLE_CLIENT_ID || ''

  return {
    plugins: [react(), sitemap()],
    envDir: '../',
    define: {
      'import.meta.env.VITE_SUPABASE_URL': JSON.stringify(supabaseUrl),
      'import.meta.env.VITE_SUPABASE_ANON_KEY': JSON.stringify(supabaseAnonKey),
      'import.meta.env.VITE_API_BASE_URL': JSON.stringify(apiBaseUrl),
      'import.meta.env.VITE_GOOGLE_CLIENT_ID': JSON.stringify(googleClientId),
    }
  }
})
