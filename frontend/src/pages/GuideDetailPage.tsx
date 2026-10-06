/**
 * GuideDetailPage — renders one markdown guide at /guides/:slug.
 *
 * Markdown is rendered with react-markdown (no raw HTML — script injection
 * is structurally impossible) + remark-gfm for tables and task lists.
 */
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Link, useParams } from 'react-router-dom'
import { getGuide } from '../lib/guides'
import { usePageMeta } from '../lib/pageMeta'

export function GuideDetailPage() {
  const { slug } = useParams<{ slug: string }>()
  const guide = slug ? getGuide(slug) : undefined
  usePageMeta({ title: guide?.title ?? 'Guide not found', description: guide?.description })

  if (!guide) {
    return (
      <div style={{ maxWidth: '720px', margin: '4rem auto', textAlign: 'center' }}>
        <h2>Guide not found</h2>
        <p style={{ color: 'var(--text-muted)' }}>
          This guide may have been moved or renamed.{' '}
          <Link to="/guides" style={{ color: 'var(--primary)' }}>Browse all guides</Link>
        </p>
      </div>
    )
  }

  return (
    <article style={{ maxWidth: '760px', margin: '0 auto' }}>
      <nav style={{ fontSize: '0.875rem', marginBottom: '1.5rem' }}>
        <Link to="/guides" style={{ color: 'var(--primary)', textDecoration: 'none' }}>← All guides</Link>
      </nav>

      <header style={{ marginBottom: '2rem' }}>
        <h1 style={{ fontSize: '2rem', marginBottom: '0.5rem' }}>{guide.title}</h1>
        <div style={{ fontSize: '0.8125rem', color: 'var(--text-muted)' }}>
          {guide.category}
          {guide.updated && <> · Updated {guide.updated}</>}
        </div>
      </header>

      <div className="guide-markdown">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{guide.body}</ReactMarkdown>
      </div>
    </article>
  )
}

export default GuideDetailPage
