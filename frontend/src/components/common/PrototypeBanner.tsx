/**
 * PrototypeBanner — sets expectations on the home page.
 *
 * Wildlife Watcher is in active development and people are testing it with
 * real field data, so the site says so plainly rather than letting a rough
 * edge come as a surprise.
 */
export function PrototypeBanner() {
  return (
    <div
      role="status"
      style={{
        display: 'flex',
        alignItems: 'flex-start',
        gap: '0.75rem',
        padding: '0.875rem 1rem',
        border: '1px solid var(--border)',
        borderLeft: '4px solid var(--primary)',
        borderRadius: 'var(--radius)',
        background: 'var(--surface)',
      }}
    >
      <span aria-hidden="true" style={{ fontSize: '1.125rem', lineHeight: 1.3 }}>
        🚧
      </span>
      <div style={{ minWidth: 0 }}>
        <strong style={{ display: 'block', fontSize: '0.9375rem', marginBottom: '0.125rem' }}>
          This is a prototype — we&apos;re still building it
        </strong>
        <span style={{ fontSize: '0.875rem', opacity: 0.75 }}>
          Features are changing week to week and you may hit rough edges. Keep your
          own copy of anything important, and tell us what breaks —{' '}
          <a href="mailto:contact@wildlife.ai?subject=Wildlife%20Watcher%20feedback">
            contact@wildlife.ai
          </a>
          .
        </span>
      </div>
    </div>
  )
}
