import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'
import { useProjectSelection } from '../hooks/useProjectSelection'
import { supabase } from '../config/supabase'
import { QRCodeSVG } from 'qrcode.react'
import { ThreeStepGuide, DEFAULT_SIGNED_IN_STEPS, DEFAULT_MARKETING_STEPS } from '../components/common/ThreeStepGuide'
import { DemoLoginButton } from '../components/common/DemoLoginButton'
import { PrototypeBanner } from '../components/common/PrototypeBanner'

const APP_STORE_URL = 'https://apps.apple.com/app/id6480342929'
const PLAY_STORE_URL = 'https://play.google.com/store/apps/details?id=com.wildlife.wildlifewatcher&pcampaignid=web_share'

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────

interface ProjectCard {
  id: string
  name: string
  description: string | null
  created_at: string
  deployment_count: number
}

// ─────────────────────────────────────────────────────────────────────────────
// ProjectCardTile (unchanged from before)
// ─────────────────────────────────────────────────────────────────────────────

function ProjectCardTile({
  project, onOpen, onHealth,
}: { project: ProjectCard; onOpen: () => void; onHealth: () => void }) {
  return (
    <div
      className="glass-card"
      onClick={onOpen}
      style={{
        padding: '1.25rem', cursor: 'pointer',
        display: 'flex', flexDirection: 'column', gap: '0.75rem',
        transition: 'transform 0.15s, box-shadow 0.15s', minWidth: 0,
        backgroundColor: 'var(--surface)', border: '1px solid var(--border)',
        borderRadius: 'var(--radius)',
      }}
      onMouseEnter={e => { e.currentTarget.style.transform = 'translateY(-3px)'; e.currentTarget.style.boxShadow = '0 8px 24px rgba(76,175,80,0.12)' }}
      onMouseLeave={e => { e.currentTarget.style.transform = 'translateY(0)';    e.currentTarget.style.boxShadow = 'none' }}
    >
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: '0.75rem' }}>
        <div style={{
          width: 40, height: 40, borderRadius: 10, flexShrink: 0,
          background: 'linear-gradient(135deg,rgba(76,175,80,0.3),rgba(76,175,80,0.08))',
          display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '1.25rem',
        }}>📂</div>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontWeight: 600, fontSize: '0.9375rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {project.name}
          </div>
          {project.description && (
            <div style={{ fontSize: '0.75rem', opacity: 0.6, marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {project.description}
            </div>
          )}
        </div>
      </div>
      <div style={{ fontSize: '0.75rem', opacity: 0.7 }}>
        <strong style={{ color: 'var(--text-color)', opacity: 1 }}>{project.deployment_count}</strong>{' '}
        {project.deployment_count === 1 ? 'deployment' : 'deployments'}
      </div>
      <div style={{ display: 'flex', gap: '0.5rem', marginTop: 'auto' }} onClick={e => e.stopPropagation()}>
        <button onClick={onOpen} className="btn" style={{ flex: 1, fontSize: '0.75rem', padding: '0.375rem 0', backgroundColor: 'var(--primary)', color: '#fff', border: 'none' }}>
          Open →
        </button>
        <button onClick={onHealth} title="Dataset health dashboard" style={{ fontSize: '0.75rem', padding: '0.375rem 0.625rem', border: '1px solid var(--border)', borderRadius: 'var(--radius)', backgroundColor: 'transparent', color: 'var(--primary)', cursor: 'pointer' }}>
          📊
        </button>
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// WS3 — Signed-in dashboard
// ─────────────────────────────────────────────────────────────────────────────

function Dashboard() {
  const { user } = useAuth()
  const navigate = useNavigate()
  // `projects` changes when the shared list reloads (e.g. an accepted
  // invitation), which refetches the cards below.
  const { clearAll, toggleProject, projects: selectableProjects } = useProjectSelection()
  const [projects, setProjects] = useState<ProjectCard[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!user) return
    let cancelled = false
    supabase
      .from('projects')
      .select('id, name, description, created_at, deployments(id)')
      .is('deleted_at', null)
      .is('deployments.deleted_at', null)
      .order('created_at', { ascending: false })
      .then(({ data }) => {
        if (cancelled) return
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        setProjects((data || []).map((p: any) => ({
          id: p.id, name: p.name, description: p.description, created_at: p.created_at,
          deployment_count: Array.isArray(p.deployments) ? p.deployments.length : 0,
        })))
        setLoading(false)
      })
    return () => { cancelled = true }
  }, [user, selectableProjects])

  const firstName = user?.email?.split('@')[0] ?? 'there'

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '2.5rem' }}>

      <PrototypeBanner />

      {/* Greeting */}
      <div>
        <h2 style={{ margin: '0 0 0.25rem 0', fontSize: '1.75rem' }}>
          Welcome back, {firstName} 👋
        </h2>
        <p style={{ margin: 0, opacity: 0.65, fontSize: '0.9375rem' }}>
          Your three-step workflow to go from SD card to results.
        </p>
      </div>

      {/* Three-step guide (WS0-T4) */}
      <ThreeStepGuide steps={DEFAULT_SIGNED_IN_STEPS} />

      {/* Project cards — quick-access when the user has projects */}
      {!loading && projects.length > 0 && (
        <div>
          <h3 style={{ margin: '0 0 1rem 0', fontSize: '1.125rem', fontWeight: 600 }}>
            Your Projects
          </h3>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: '1rem' }}>
            {projects.map(p => (
              <ProjectCardTile
                key={p.id}
                project={p}
                onOpen={() => { clearAll(); toggleProject(p.id); navigate('/insights?tab=deployments') }}
                onHealth={() => navigate(`/intelligence/${p.id}`)}
              />
            ))}
          </div>
        </div>
      )}

      {/* WS3-T2: New user empty state — deep-link to Results > Projects with create modal open */}
      {!loading && projects.length === 0 && (
        <div style={{
          backgroundColor: 'var(--surface)', border: '1px solid var(--border)',
          borderRadius: 'var(--radius)', padding: '2.5rem', textAlign: 'center',
        }}>
          <div style={{ fontSize: '2.5rem', marginBottom: '0.75rem' }}>📷</div>
          <h3 style={{ marginTop: 0 }}>No projects yet</h3>
          <p style={{ opacity: 0.7, fontSize: '0.9rem', maxWidth: '400px', margin: '0 auto 1.5rem' }}>
            Create your first project to start managing deployments and reviewing results.
            You can also manage projects from the{' '}
            <a href={APP_STORE_URL} target="_blank" rel="noreferrer" style={{ color: 'var(--primary)' }}>Wildlife Watcher mobile app</a>.
          </p>
          <div style={{ display: 'flex', gap: '0.75rem', justifyContent: 'center', flexWrap: 'wrap' }}>
            {/* ?create=true causes SettingsPage to auto-open the CreateProjectModal */}
            <Link to="/settings?create=true" className="btn" style={{ textDecoration: 'none' }}>
              + Create Project
            </Link>
            <Link to="/upload-data" style={{ textDecoration: 'none', padding: '0.5rem 1rem', border: '1px solid var(--border)', borderRadius: 'var(--radius)', color: 'var(--text-color)', fontSize: '0.9rem' }}>
              ⬆ Upload anyway
            </Link>
          </div>
        </div>
      )}

    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// WS1 — Logged-out marketing page
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
        <p style={{ fontSize: '1.25rem', opacity: 0.8, marginBottom: '1rem' }}>
          The Wildlife Watcher is a compact camera designed to monitor invertebrates and
          small animals that traditional camera traps miss. On-device AI flags your target
          species in the field, and an open-source website makes analysis and reporting easy.
        </p>
        <p style={{ fontSize: '0.9375rem', opacity: 0.6, marginBottom: '2rem', fontWeight: 600, letterSpacing: '0.01em' }}>
          Smart monitoring of small animals · on-device AI · open-source web analysis
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

      {/* ── Get the Mobile App ──────────────────────────────────────────── */}
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '3rem 0' }}>
        <h2 style={{ fontSize: '1.5rem', marginBottom: '1.5rem', fontWeight: 600 }}>Get the Mobile App</h2>
        <div style={{ display: 'flex', justifyContent: 'center', gap: '4rem', flexWrap: 'wrap' }}>
          <div style={{ textAlign: 'center' }}>
            <div style={{ marginBottom: '1rem' }}>
              <a href={APP_STORE_URL} target="_blank" rel="noreferrer">
                <img src="https://developer.apple.com/assets/elements/badges/download-on-the-app-store.svg" alt="Download on the App Store" style={{ height: '40px' }} />
              </a>
            </div>
            <div style={{ padding: '1rem', backgroundColor: 'white', borderRadius: '12px', display: 'inline-block', boxShadow: '0 4px 12px rgba(0,0,0,0.1)' }}>
              <QRCodeSVG value={APP_STORE_URL} size={150} />
            </div>
          </div>
          <div style={{ textAlign: 'center' }}>
            <div style={{ marginBottom: '1rem' }}>
              <a href={PLAY_STORE_URL} target="_blank" rel="noreferrer">
                <img src="https://upload.wikimedia.org/wikipedia/commons/7/78/Google_Play_Store_badge_EN.svg" alt="Get it on Google Play" style={{ height: '40px' }} />
              </a>
            </div>
            <div style={{ padding: '1rem', backgroundColor: 'white', borderRadius: '12px', display: 'inline-block', boxShadow: '0 4px 12px rgba(0,0,0,0.1)' }}>
              <QRCodeSVG value={PLAY_STORE_URL} size={150} />
            </div>
          </div>
        </div>
      </div>

      {/* ── Section 1: Why ──────────────────────────────────────────────── */}
      <div id="why" style={SECTION_STYLE}>
        <div style={{ maxWidth: '760px', margin: '0 auto', padding: '0 1rem' }}>
          <h2 style={SECTION_HEADING}>Why Wildlife Watcher?</h2>
          <p style={{ opacity: 0.75, fontSize: '1rem', lineHeight: 1.65, marginBottom: '1.5rem' }}>
            Running a camera-trap survey means juggling hundreds — sometimes tens of thousands — of
            photos, cryptic file names, and manual species identification that takes weeks.
            Wildlife Watcher is an end-to-end toolkit that solves the three hardest problems:
          </p>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '1rem', marginBottom: '1.75rem' }}>
            {[
              { icon: '📷', title: 'The hardware', body: 'Compact, weatherproof AI cameras built for small wildlife, auto-tagging images with deployment metadata.', anchor: '#watchers' },
              { icon: '📱', title: 'The app',      body: 'Configure devices and manage projects from the field on iOS or Android.',               anchor: '#app' },
              { icon: '🌐', title: 'The web',      body: 'Upload images, review AI detections, group look-alikes, and export publication-ready reports.', anchor: '#web' },
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
                  <div style={{ fontSize: '1.5rem', marginBottom: '0.5rem' }}>{item.icon}</div>
                  <div style={{ fontWeight: 600, marginBottom: '0.375rem' }}>{item.title}</div>
                  <p style={{ margin: 0, fontSize: '0.875rem', opacity: 0.7, lineHeight: 1.5 }}>{item.body}</p>
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
          <p style={{ opacity: 0.75, fontSize: '1rem', lineHeight: 1.65, marginBottom: '1.25rem' }}>
            Wildlife Watchers are open-hardware camera traps designed for conservation science —
            specifically for the invertebrates and small cold-blooded animals (skinks, frogs,
            wētā) that conventional thermal-trigger cameras miss. Each device is weatherproof,
            runs about a month in the field on 4× AA batteries, and embeds a deployment ID and
            GPS coordinates directly in every image — so the web platform knows exactly where
            and when each photo was taken without any manual matching.
          </p>
          <ul style={{ paddingLeft: '1.25rem', opacity: 0.8, lineHeight: 1.85, marginBottom: '1.5rem' }}>
            <li>A lightweight on-device AI model (Camera AI) flags your target species in real time; the more powerful SpeciesNet model (Cloud AI) assists with full identification when you upload, and the Wildlife Brain groups look-alike animals so large datasets are quick to review.</li>
            <li>LoRaWAN telemetry (in development) will send battery and SD-card status back to the dashboard.</li>
            <li>Fully open hardware — schematics and firmware published on GitHub.</li>
          </ul>
          <p style={{ opacity: 0.7, fontSize: '0.9rem', lineHeight: 1.6, marginBottom: '1rem' }}>
            Wildlife Watcher is currently in a prototype phase — devices are available to Beta
            testers. <Link to="/faq#buy" style={{ color: 'var(--primary)' }}>How to get one →</Link>
          </p>
          <a
            href="https://wildlife.ai/wildlife-watcher"
            target="_blank"
            rel="noreferrer"
            className="btn"
            style={{ textDecoration: 'none', display: 'inline-block' }}
          >
            Learn more →
          </a>
        </div>
      </div>

      {/* ── Section 3: The Wildlife Watcher App ─────────────────────────── */}
      <div id="app" style={SECTION_STYLE}>
        <div style={{ maxWidth: '760px', margin: '0 auto', padding: '0 1rem' }}>
          <h2 style={SECTION_HEADING}>The Wildlife Watcher App</h2>
          <p style={{ opacity: 0.75, fontSize: '1rem', lineHeight: 1.65, marginBottom: '1.25rem' }}>
            The companion mobile app (iOS &amp; Android) is your field tool.
            Use it to create projects, define deployments, and pair and control devices over
            Bluetooth — all without a laptop.
            Projects and deployments created in the app are immediately available on the web.
          </p>
          <ul style={{ paddingLeft: '1.25rem', opacity: 0.8, lineHeight: 1.85, marginBottom: '1.75rem' }}>
            <li>Create and manage projects with team role management (admin, field worker, analyst, viewer).</li>
            <li>Define deployment locations and configure camera settings.</li>
            <li>Pair and provision Wildlife Watcher devices over Bluetooth, with a live preview while monitoring.</li>
            <li>Remote battery and SD-card telemetry over LoRaWAN is in development.</li>
          </ul>
          {/* Reuse app-store badges inline */}
          <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', alignItems: 'center' }}>
            <a href={APP_STORE_URL} target="_blank" rel="noreferrer">
              <img src="https://developer.apple.com/assets/elements/badges/download-on-the-app-store.svg" alt="Download on the App Store" style={{ height: '36px' }} />
            </a>
            <a href={PLAY_STORE_URL} target="_blank" rel="noreferrer">
              <img src="https://upload.wikimedia.org/wikipedia/commons/7/78/Google_Play_Store_badge_EN.svg" alt="Get it on Google Play" style={{ height: '36px' }} />
            </a>
          </div>
        </div>
      </div>

      {/* ── Section 4: The Wildlife Watcher Web ─────────────────────────── */}
      <div id="web" style={SECTION_STYLE}>
        <div style={{ maxWidth: '760px', margin: '0 auto', padding: '0 1rem' }}>
          <h2 style={SECTION_HEADING}>The Wildlife Watcher Web</h2>
          <p style={{ opacity: 0.75, fontSize: '1rem', lineHeight: 1.65, marginBottom: '2rem' }}>
            Once your devices are in the field and taking photos, the web platform handles
            everything from raw image upload to publication-ready reports in three steps.
          </p>
          <ThreeStepGuide steps={DEFAULT_MARKETING_STEPS} />
          <div style={{ marginTop: '2rem', textAlign: 'center' }}>
            <Link to="/signup" className="btn" style={{ textDecoration: 'none', padding: '0.75rem 2.5rem', fontSize: '1rem' }}>
              Get started — it's free
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
  if (loading) return <div style={{ padding: '3rem', textAlign: 'center', opacity: 0.5 }}>Loading…</div>
  return user ? <Dashboard /> : <MarketingHero />
}
