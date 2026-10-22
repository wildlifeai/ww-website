import { lazy } from 'react'
import { Link } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'
import { ThreeStepGuide } from '../components/common/ThreeStepGuide'
import { DEFAULT_MARKETING_STEPS } from '../components/common/guideSteps'
import { DemoLoginButton } from '../components/common/DemoLoginButton'
import { PrototypeBanner } from '../components/common/PrototypeBanner'
import { StoreBadges } from '../components/common/StoreBadges'
import { Camera, Globe, Smartphone } from 'lucide-react'
import { usePageMeta } from '../lib/pageMeta'
import { mayHaveSession } from '../lib/authUser'

// The signed-in dashboard is lazy like every signed-in page, so a visitor does not download it.
const HomeDashboard = lazy(() => import('./HomeDashboard').then(m => ({ default: m.HomeDashboard })))

// ─────────────────────────────────────────────────────────────────────────────
// WS1: logged-out marketing page
// ─────────────────────────────────────────────────────────────────────────────

const SECTION_STYLE: React.CSSProperties = {
  padding: '4rem 0',
  borderTop: '1px solid var(--border)',
}

const SECTION_HEADING: React.CSSProperties = {
  fontSize: '1.625rem',
  fontWeight: 700,
  marginBottom: '0.75rem',
  marginTop: 0,
}

function MarketingHero() {
  usePageMeta({ description: 'The Wildlife Watcher is a compact open-source AI camera for invertebrates and small animals, with a mobile app for the field and a website for review, analysis and reporting.' })
  return (
    <div>
      <div style={{ maxWidth: '800px', margin: '0 auto 2rem' }}>
        <PrototypeBanner />
      </div>

      {/* ── Hero ────────────────────────────────────────────────────────── */}
      <div style={{ textAlign: 'center', maxWidth: '800px', margin: '0 auto', padding: '0 0 3rem 0' }}>
        <h1 style={{ fontSize: '3rem', color: 'var(--primary)', marginBottom: '1rem' }}>
          Smart monitoring of small animals
        </h1>
        <p style={{ fontSize: '1.25rem', color: 'var(--text-muted)', marginBottom: '2rem' }}>
          The Wildlife Watcher is a compact camera designed to monitor invertebrates and
          small animals that traditional camera traps miss. On-device AI flags your target
          species in the field, and an open-source website makes analysis and reporting easy.
        </p>
        <div style={{ display: 'flex', gap: '1rem', justifyContent: 'center', alignItems: 'flex-start', flexWrap: 'wrap' }}>
          <Link
            to="/login"
            className="btn"
            id="hero-login-button"
            style={{
              display: 'inline-block',
              padding: '0.875rem 2.5rem',
              fontSize: '1.125rem',
              fontWeight: 600,
              textDecoration: 'none',
              borderRadius: 'var(--radius)',
              boxShadow: '0 4px 14px rgba(0,110,28,0.3)',
            }}
          >
            Log in to get started
          </Link>
          <DemoLoginButton style={{ padding: '0.875rem 2.5rem', fontSize: '1.125rem' }} />
        </div>
      </div>

      {/* ── Section 1: Why ──────────────────────────────────────────────── */}
      <div id="why" style={SECTION_STYLE}>
        <div style={{ maxWidth: '760px', margin: '0 auto', padding: '0 1rem' }}>
          <h2 style={SECTION_HEADING}>Why Wildlife Watcher?</h2>
          <p style={{ color: 'var(--text-muted)', fontSize: '1rem', lineHeight: 1.65, marginBottom: '1.5rem' }}>
            Running a camera-trap survey means juggling hundreds, sometimes tens of thousands, of
            photos, cryptic file names, and manual species identification that takes weeks.
            Wildlife Watcher is an end-to-end toolkit that solves the three hardest problems:
          </p>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '1rem', marginBottom: '1.75rem' }}>
            {[
              { icon: Camera, title: 'The hardware', body: 'Compact, weatherproof AI cameras built for small wildlife, auto-tagging images with deployment metadata.', anchor: '#watchers' },
              { icon: Smartphone, title: 'The app',      body: 'Configure devices and manage projects from the field on iOS or Android.',               anchor: '#app' },
              { icon: Globe, title: 'The web',      body: 'Upload images, review AI detections, group look-alikes, and export publication-ready reports.', anchor: '#web' },
            ].map(item => (
              <a key={item.anchor} href={item.anchor} style={{ textDecoration: 'none', color: 'inherit' }}>
                <div style={{
                  backgroundColor: 'var(--surface)', border: '1px solid var(--border)',
                  borderRadius: 'var(--radius)', padding: '1.25rem',
                  transition: 'border-color 0.15s',
                }}
                  onMouseEnter={e => ((e.currentTarget as HTMLElement).style.borderColor = 'var(--primary)')}
                  onMouseLeave={e => ((e.currentTarget as HTMLElement).style.borderColor = 'var(--border)')}
                >
                  <div style={{ marginBottom: '0.625rem', color: 'var(--primary)' }}><item.icon size={26} aria-hidden="true" /></div>
                  <div style={{ fontWeight: 600, marginBottom: '0.375rem' }}>{item.title}</div>
                  <p style={{ margin: 0, fontSize: '0.875rem', color: 'var(--text-muted)', lineHeight: 1.5 }}>{item.body}</p>
                  <div style={{ marginTop: '0.75rem', fontSize: '0.8125rem', color: 'var(--primary)', fontWeight: 500 }}>
                    Learn more ↓
                  </div>
                </div>
              </a>
            ))}
          </div>
        </div>
      </div>

      {/* ── Section 2: The Wildlife Watchers ────────────────────────────── */}
      <div id="watchers" style={SECTION_STYLE}>
        <div style={{ maxWidth: '760px', margin: '0 auto', padding: '0 1rem' }}>
          <h2 style={SECTION_HEADING}>The Wildlife Watchers</h2>
          <p style={{ color: 'var(--text-muted)', fontSize: '1rem', lineHeight: 1.65, marginBottom: '1.25rem' }}>
            Wildlife Watchers are open-hardware camera traps designed for conservation science,
            specifically for the invertebrates and small cold-blooded animals (skinks, frogs,
            wētā) that conventional thermal-trigger cameras miss. Each device is weatherproof,
            runs about a month in the field on 4× AA batteries, and embeds a deployment ID and
            GPS coordinates directly in every image, so the web platform knows exactly where
            and when each photo was taken without any manual matching.
          </p>
          <ul style={{ paddingLeft: '1.25rem', color: 'var(--text-muted)', lineHeight: 1.85, marginBottom: '1.5rem' }}>
            <li>A lightweight on-device AI model (Camera AI) flags your target species in real time; the more powerful SpeciesNet model (Cloud AI) assists with full identification when you upload, and the Wildlife Brain groups look-alike animals so large datasets are quick to review.</li>
            <li>LoRaWAN telemetry (in development) will send battery and SD-card status back to the dashboard.</li>
            <li>Fully open hardware: schematics and firmware published on GitHub.</li>
          </ul>
          <p style={{ color: 'var(--text-muted)', fontSize: '0.9rem', lineHeight: 1.6, marginBottom: '1rem' }}>
            Wildlife Watcher is currently in a prototype phase: devices are available to Beta
            testers. <Link to="/faq#buy" style={{ color: 'var(--primary)' }}>How to get one →</Link>
          </p>
          <a
            href="https://wildlife.ai/wildlife-watcher"
            target="_blank"
            rel="noreferrer"
            className="btn"
            style={{ textDecoration: 'none', display: 'inline-block' }}
          >
            The Wildlife Watcher at wildlife.ai
          </a>
        </div>
      </div>

      {/* ── Section 3: The Wildlife Watcher App. The store badges live here, once,
          after the reader knows what the app is for; the QR codes that used to sit
          under the hero went with them (a phone taps the badge, a desktop reads
          this section first). */}
      <div id="app" style={SECTION_STYLE}>
        <div style={{ maxWidth: '760px', margin: '0 auto', padding: '0 1rem' }}>
          <h2 style={SECTION_HEADING}>The Wildlife Watcher App</h2>
          <p style={{ color: 'var(--text-muted)', fontSize: '1rem', lineHeight: 1.65, marginBottom: '1.25rem' }}>
            The companion mobile app (iOS &amp; Android) is your field tool.
            Use it to create projects, define deployments, and pair and control devices over
            Bluetooth, all without a laptop.
            Projects and deployments created in the app are immediately available on the web.
          </p>
          <ul style={{ paddingLeft: '1.25rem', color: 'var(--text-muted)', lineHeight: 1.85, marginBottom: '1.75rem' }}>
            <li>Create and manage projects, and invite your team as admins or members.</li>
            <li>Define deployment locations and configure camera settings.</li>
            <li>Pair and provision Wildlife Watcher devices over Bluetooth, with a live preview while monitoring.</li>
            <li>Remote battery and SD-card telemetry over LoRaWAN is in development.</li>
          </ul>
          <StoreBadges height={36} style={{ justifyContent: 'flex-start' }} />
        </div>
      </div>

      {/* ── Section 4: The Wildlife Watcher Web ─────────────────────────── */}
      <div id="web" style={SECTION_STYLE}>
        <div style={{ maxWidth: '760px', margin: '0 auto', padding: '0 1rem' }}>
          <h2 style={SECTION_HEADING}>The Wildlife Watcher Web</h2>
          <p style={{ color: 'var(--text-muted)', fontSize: '1rem', lineHeight: 1.65, marginBottom: '2rem' }}>
            Once your devices are in the field and taking photos, the web platform handles
            everything from raw image upload to publication-ready reports in three steps.
          </p>
          <ThreeStepGuide steps={DEFAULT_MARKETING_STEPS} />
          <div style={{ marginTop: '2rem', textAlign: 'center' }}>
            <Link to="/signup" className="btn" style={{ textDecoration: 'none', padding: '0.75rem 2.5rem', fontSize: '1rem' }}>
              Get started, it's free
            </Link>
          </div>
        </div>
      </div>

    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Root export
// ─────────────────────────────────────────────────────────────────────────────

export function HomePage() {
  const { user, loading } = useAuth()
  // A visitor with no session gets the marketing page at once, not a "Loading…" that it replaces.
  if (loading && mayHaveSession()) return <div style={{ padding: '3rem', textAlign: 'center', color: 'var(--text-muted)' }}>Loading…</div>
  return user ? <HomeDashboard /> : <MarketingHero />
}
