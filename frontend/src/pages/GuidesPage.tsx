/**
 * GuidesPage — public index of all advanced guides (/guides), grouped by
 * category. Content comes from src/content/guides/*.md via the build-time
 * loader. Everyday setup lives under /resources; these guides target users
 * pushing past the defaults (custom models, novel device setups).
 */
import { Link } from 'react-router-dom'
import { guidesByCategory } from '../lib/guides'

export function GuidesPage() {
  const grouped = guidesByCategory()

  return (
    <div style={{ maxWidth: '800px', margin: '0 auto' }}>
      <div style={{ marginBottom: '2rem' }}>
        <h1 style={{ fontSize: '2rem', marginBottom: '0.5rem' }}>📖 Advanced Guides</h1>
        <p style={{ color: 'var(--text-muted)', maxWidth: '640px', lineHeight: 1.6 }}>
          Guidance and best practices from the Wildlife Watcher team for taking the platform
          further — developing and training your own AI models, and setting up devices in novel
          ways for your monitoring goals. Looking for the basics? Start with{' '}
          <Link to="/resources" style={{ color: 'var(--primary)' }}>Resources</Link>.
        </p>
      </div>

      {grouped.size === 0 && (
        <p style={{ color: 'var(--text-muted)' }}>No guides published yet — check back soon.</p>
      )}

      {[...grouped.entries()].map(([category, items]) => (
        <section key={category} style={{ marginBottom: '2.5rem' }}>
          <h2 style={{ fontSize: '1.25rem', marginBottom: '1rem', paddingBottom: '0.5rem', borderBottom: '2px solid var(--primary)' }}>
            {category}
          </h2>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
            {items.map(g => (
              <Link
                key={g.slug}
                to={`/guides/${g.slug}`}
                style={{
                  display: 'block', padding: '1rem 1.25rem', textDecoration: 'none',
                  color: 'var(--text-color)', backgroundColor: 'var(--surface)',
                  border: '1px solid var(--border)', borderRadius: 'var(--radius)',
                  transition: 'border-color 0.15s',
                }}
                onMouseEnter={e => ((e.currentTarget as HTMLElement).style.borderColor = 'var(--primary)')}
                onMouseLeave={e => ((e.currentTarget as HTMLElement).style.borderColor = 'var(--border)')}
              >
                <div style={{ fontWeight: 600, marginBottom: g.description ? '0.25rem' : 0 }}>{g.title}</div>
                {g.description && (
                  <div style={{ fontSize: '0.875rem', opacity: 0.7, lineHeight: 1.5 }}>{g.description}</div>
                )}
                {g.updated && (
                  <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.375rem' }}>Updated {g.updated}</div>
                )}
              </Link>
            ))}
          </div>
        </section>
      ))}
    </div>
  )
}

export default GuidesPage
