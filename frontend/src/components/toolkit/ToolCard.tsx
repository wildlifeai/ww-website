/**
 * ToolCard: one tool on the Toolkit page, an icon, a title, one line of description and the
 * tool's controls. A tool that may not show (the iNaturalist panel when the feature is off)
 * renders its own ToolCard, so nothing is left as an empty card (UI audit 01 D4).
 */
import type { ReactNode } from 'react'
import type { LucideIcon } from 'lucide-react'

export function ToolCard({ icon: Icon, title, description, children, comingSoon = false }: {
  icon: LucideIcon
  title: string
  description: string
  children?: ReactNode
  comingSoon?: boolean
}) {
  return (
    <div className="card" style={{ padding: '1.5rem', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: '0.875rem' }}>
        <div style={{
          width: 44, height: 44, borderRadius: 10, flexShrink: 0, color: 'var(--primary)',
          background: 'linear-gradient(135deg,rgba(76,175,80,0.25),rgba(76,175,80,0.07))',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}><Icon size={22} aria-hidden="true" /></div>
        <div>
          <div style={{ fontWeight: 600, fontSize: '1rem', marginBottom: '0.25rem' }}>
            {title}
            {comingSoon && (
              <span style={{
                marginLeft: '0.5rem', fontSize: '0.6875rem', fontWeight: 500, padding: '0.15rem 0.5rem',
                borderRadius: '12px', border: '1px solid var(--border)', color: 'var(--text-muted)',
              }}>coming soon</span>
            )}
          </div>
          <p style={{ margin: 0, fontSize: '0.875rem', color: 'var(--text-muted)', lineHeight: 1.5 }}>{description}</p>
        </div>
      </div>
      {children && <div>{children}</div>}
    </div>
  )
}
