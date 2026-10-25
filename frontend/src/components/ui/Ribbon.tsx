/**
 * Ribbon: a Microsoft-Word-style command surface.
 *
 *   ┌───────────────────────────────────────────────────────────────┐
 *   │ [ Tab ][ Tab ][ Tab ]                             status… │  ← menu bar
 *   ├───────────────────────────────────────────────────────────────┤
 *   │  ⌄group⌄ │ ⌄group⌄ │ ⌄group⌄                    ⤢          │  ← ribbon body
 *   │  controls  controls   controls                              │
 *   │  CAPTION    CAPTION     CAPTION                              │
 *   └───────────────────────────────────────────────────────────────┘
 *
 * - **Menu bar**: ribbon tabs (controlled or uncontrolled) + an optional
 *   right-aligned status slot. The header carries the brand; the ribbon has no mark of its own.
 * - **Narrow screens**: below 900px the groups wrap onto more rows (`.ribbon-body` in index.css).
 * - **Ribbon body**: the active tab's grouped categories. Each group has its
 *   controls on top and a small caption underneath, divided like Word's ribbon.
 * - **Advanced settings**: an optional per-group dialog launcher (the ⤢ arrow),
 *   mirroring Word's group dialog-launcher.
 *
 * Used by the Annotations and Results command bars.
 */
import { useState } from 'react'

export interface RibbonGroupDef {
  id: string
  /** Caption shown beneath the group. */
  title: string
  content: React.ReactNode
  /** Optional dialog launcher (advanced settings) — renders the ⤢ corner arrow. */
  launcher?: () => void
  launcherTitle?: string
  /** Show a dot on the launcher to signal active advanced settings. */
  launcherActive?: boolean
}

export interface RibbonTabDef {
  id: string
  label: string
  /** A short glyph or a lucide icon element before the label. */
  icon?: React.ReactNode
  groups: RibbonGroupDef[]
}

export interface RibbonProps {
  tabs: RibbonTabDef[]
  /** Controlled active tab (e.g. wired to the page's own tab state). */
  activeTabId?: string
  /** Uncontrolled initial tab. */
  defaultTabId?: string
  onTabChange?: (id: string) => void
  /** Optional short wordmark before the tabs. */
  brandLabel?: string
  /** Right-aligned status content in the menu bar (e.g. result counts). */
  status?: React.ReactNode
  /** Right-aligned controls in the body row (e.g. view toggle, thumbnail size).
   *  Must not contain pop-overs/dropdowns — the ribbon container clips overflow. */
  aside?: React.ReactNode
  /** A full-width row pinned below the body, inside the sticky container (e.g. the
   *  selection/actions bar) so it stays visible while the grid scrolls. Any
   *  dropdown it renders must use a portal (the container clips overflow). */
  subBar?: React.ReactNode
  /** Keep the ribbon pinned below the app header while the page scrolls. */
  sticky?: boolean
  /** Offset from the top when sticky (defaults to the 56px app header height). */
  stickyTop?: number
}

// ── Styles ───────────────────────────────────────────────────────────────────

const CONTAINER: React.CSSProperties = {
  border: '1px solid var(--border)',
  borderRadius: 'var(--radius)',
  overflow: 'hidden',
  backgroundColor: 'var(--surface)',
  marginBottom: '1.25rem',
  boxShadow: '0 1px 2px rgba(0,0,0,0.04)',
}

const MENUBAR: React.CSSProperties = {
  display: 'flex',
  alignItems: 'stretch',
  borderBottom: '1px solid var(--border)',
  background: 'linear-gradient(180deg, rgba(76,175,80,0.07), rgba(76,175,80,0))',
  minHeight: 40,
}

const BRAND_WORD: React.CSSProperties = {
  display: 'flex', alignItems: 'center', padding: '0 0.875rem', borderRight: '1px solid var(--border)',
  fontSize: '0.8125rem', fontWeight: 700, letterSpacing: '0.02em', whiteSpace: 'nowrap', flexShrink: 0,
}

const STATUS: React.CSSProperties = {
  marginLeft: 'auto', display: 'flex', alignItems: 'center',
  fontSize: '0.75rem', padding: '0 0.875rem', whiteSpace: 'nowrap',
}

// No overflowX here: .ribbon-body scrolls wide rows and wraps them below 900px (index.css).
const BODY: React.CSSProperties = {
  display: 'flex', alignItems: 'stretch',
  padding: '0.4rem 0.25rem',
}

const GROUP: React.CSSProperties = {
  position: 'relative', display: 'flex', flexDirection: 'column',
  justifyContent: 'space-between', padding: '0.3rem 0.875rem 0.2rem', minHeight: 62,
}

const GROUP_CONTENT: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap', flex: 1,
}

const GROUP_CAPTION: React.CSSProperties = {
  textAlign: 'center', fontSize: '0.625rem', textTransform: 'uppercase',
  letterSpacing: '0.05em', color: 'var(--text-muted)', marginTop: 6, fontWeight: 600, whiteSpace: 'nowrap',
}

const LAUNCHER: React.CSSProperties = {
  position: 'absolute', top: 2, right: 2, width: 18, height: 18,
  border: 'none', background: 'transparent', cursor: 'pointer',
  fontSize: '0.7rem', lineHeight: 1, color: 'var(--text-color)', opacity: 0.45, padding: 0,
}

function tabStyle(active: boolean): React.CSSProperties {
  return {
    padding: '0 1rem', minHeight: 40, border: 'none', background: active ? 'var(--surface)' : 'transparent',
    cursor: 'pointer', fontSize: '0.8125rem', whiteSpace: 'nowrap',
    color: active ? 'var(--primary)' : 'var(--text-muted)',
    fontWeight: active ? 700 : 500,
    boxShadow: active ? 'inset 0 -3px 0 0 var(--primary)' : 'none',
    transition: 'background 0.12s, color 0.12s',
  }
}

// ── Component ────────────────────────────────────────────────────────────────

export function Ribbon({ tabs, activeTabId, defaultTabId, onTabChange, brandLabel, status, aside, subBar, sticky, stickyTop = 56 }: RibbonProps) {
  const [internal, setInternal] = useState(defaultTabId ?? tabs[0]?.id)
  const active = activeTabId ?? internal
  const setActive = (id: string) => { onTabChange?.(id); if (activeTabId === undefined) setInternal(id) }
  const current = tabs.find(t => t.id === active) ?? tabs[0]
  const hasBody = (current?.groups.length ?? 0) > 0 || aside != null

  // Clicking the active tab collapses/expands the filter row; clicking another
  // tab switches to it (and expands).
  const [collapsed, setCollapsed] = useState(false)
  const onTabClick = (id: string) => {
    if (id === active) setCollapsed(c => !c)
    else { setActive(id); setCollapsed(false) }
  }

  // Pin below the app header (z 100) so it stays usable while the grid scrolls.
  // Sits under the full-screen modal (z 300) and slide-overs (z 200).
  const containerStyle: React.CSSProperties = sticky
    ? { ...CONTAINER, position: 'sticky', top: stickyTop, zIndex: 50 }
    : CONTAINER

  return (
    <div style={containerStyle}>
      {/* ── Menu bar ──────────────────────────────────────── */}
      <div style={MENUBAR}>
        {brandLabel && <span style={BRAND_WORD}>{brandLabel}</span>}
        <div style={{ display: 'flex', alignItems: 'stretch', flexWrap: 'wrap' }}>
          {tabs.map(t => {
            const on = t.id === active
            return (
              <button
                key={t.id}
                onClick={() => onTabClick(t.id)}
                title={on && hasBody ? (collapsed ? 'Show filters' : 'Hide filters') : undefined}
                style={tabStyle(on)}
                onMouseEnter={e => { if (!on) e.currentTarget.style.background = 'rgba(0,0,0,0.04)' }}
                onMouseLeave={e => { if (!on) e.currentTarget.style.background = 'transparent' }}
              >
                {t.icon && <span style={{ marginRight: 6 }}>{t.icon}</span>}{t.label}
                {on && hasBody && <span style={{ marginLeft: 6, fontSize: '0.6rem', opacity: 0.6 }}>{collapsed ? '▸' : '▾'}</span>}
              </button>
            )
          })}
        </div>
        {status != null && <div style={STATUS}>{status}</div>}
      </div>

      {/* ── Ribbon body (collapsible via the active tab). A tab with no groups and no aside
          has no body at all, rather than an empty row. ── */}
      {!collapsed && hasBody && (
      <div className="ribbon-body" style={BODY}>
        {current?.groups.map((g, i) => (
          <div
            key={g.id}
            style={{ ...GROUP, borderRight: i < current.groups.length - 1 ? '1px solid var(--border)' : 'none' }}
          >
            {g.launcher && (
              <button
                onClick={g.launcher}
                title={g.launcherTitle ?? 'More options'}
                style={{ ...LAUNCHER, opacity: g.launcherActive ? 0.95 : 0.45, color: g.launcherActive ? 'var(--primary)' : 'var(--text-color)' }}
                onMouseEnter={e => (e.currentTarget.style.opacity = '1')}
                onMouseLeave={e => (e.currentTarget.style.opacity = g.launcherActive ? '0.95' : '0.45')}
              >
                {g.launcherActive ? '⤢●' : '⤢'}
              </button>
            )}
            <div style={GROUP_CONTENT}>{g.content}</div>
            <div style={GROUP_CAPTION}>{g.title}</div>
          </div>
        ))}
        {aside != null && (
          <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: '0.6rem', padding: '0 0.5rem 0 0.875rem', flexShrink: 0 }}>
            {aside}
          </div>
        )}
      </div>
      )}

      {/* ── Sub-bar (e.g. selection/actions) — stays sticky with the ribbon ── */}
      {subBar != null && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap', padding: '0.4rem 0.875rem', borderTop: '1px solid var(--border)', background: 'var(--surface)' }}>
          {subBar}
        </div>
      )}
    </div>
  )
}
