import { useCallback, useEffect, useMemo, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useReviewQueue, useReviewDecision, type ReviewDecision } from '../hooks/useBrain'
import { useMediaRegistry } from '../hooks/useMediaRegistry'

export function ReviewQueuePage() {
  const { deployment_id } = useParams<{ deployment_id: string }>()
  const { data, isLoading } = useReviewQueue(deployment_id)
  const registryQ = useMediaRegistry(deployment_id)
  const decide = useReviewDecision()

  const [idx, setIdx] = useState(0)
  const [name, setName] = useState('')
  const [primedFor, setPrimedFor] = useState<string | undefined>(undefined)

  const queue = useMemo(() => data?.queue || [], [data])
  const current = queue[idx]

  const previewById = useMemo(() => {
    const m: Record<string, string | null> = {}
    for (const x of registryQ.data?.media || []) m[x.id] = x.preview_url || x.thumbnail_url
    return m
  }, [registryQ.data])

  // Prefill the species field with the AI label when the item changes
  // (reset-state-during-render pattern — no effect, per React guidance).
  if (current && current.media_id !== primedFor) {
    setPrimedFor(current.media_id)
    setName(current.ai_label || '')
  }

  const advance = useCallback(() => setIdx((i) => i + 1), [])

  const submit = useCallback(
    async (decision: ReviewDecision) => {
      if (!current) return
      const sci = name.trim()
      if (decision !== 'expert' && !sci) return // approve/reassign need a name
      await decide.mutateAsync({ mediaId: current.media_id, decision, scientific_name: decision === 'expert' ? undefined : sci })
      advance()
    },
    [current, name, decide, advance],
  )

  // Keyboard: A approve · R reassign · E expert · S skip
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement) return
      if (e.key === 'a') submit('approve')
      else if (e.key === 'r') submit('reassign')
      else if (e.key === 'e') submit('expert')
      else if (e.key === 's') advance()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [submit, advance])

  if (isLoading) return <div style={{ padding: '2rem' }}>Loading review queue…</div>

  if (queue.length === 0) {
    return (
      <div className="glass-card" style={{ padding: '2rem', textAlign: 'center', maxWidth: 520, margin: '2rem auto' }}>
        <h3 style={{ marginTop: 0 }}>Review queue empty</h3>
        <p style={{ opacity: 0.8, fontSize: '0.9rem' }}>
          Run the Wildlife Brain and active-learning scoring on this deployment first.
        </p>
        <Link to={`/clusters/${deployment_id}`} className="btn" style={{ backgroundColor: 'var(--primary)', textDecoration: 'none' }}>Go to Clusters</Link>
      </div>
    )
  }

  if (idx >= queue.length) {
    return (
      <div className="glass-card" style={{ padding: '2rem', textAlign: 'center', maxWidth: 520, margin: '2rem auto' }}>
        <span style={{ fontSize: '2.5rem' }}>🎉</span>
        <h3>Review complete</h3>
        <p style={{ opacity: 0.8, fontSize: '0.9rem' }}>You reviewed all {queue.length} prioritised items.</p>
        <Link to={`/clusters/${deployment_id}`} className="btn" style={{ backgroundColor: 'var(--primary)', textDecoration: 'none' }}>Back to Clusters</Link>
      </div>
    )
  }

  const previewUrl = previewById[current.media_id]
  const reasons: string[] = []
  if (current.is_outlier) reasons.push('outlier')
  if ((current.cluster_confidence ?? 1) < 0.5) reasons.push('novel')
  if ((current.ai_confidence ?? 1) < 0.5) reasons.push('low AI confidence')
  if (current.ai_label && current.human_label && current.ai_label.toLowerCase() !== current.human_label.toLowerCase()) reasons.push('AI/human mismatch')

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem', color: 'var(--text-color)', maxWidth: 720, margin: '0 auto' }}>
      <div className="glass-card" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '0.75rem 1.25rem' }}>
        <div>
          <h3 style={{ margin: 0 }}>Review Queue</h3>
          <span style={{ fontSize: '0.8125rem', opacity: 0.7 }}>{idx + 1} / {queue.length} · ranked by active-learning score</span>
        </div>
        <Link to={`/clusters/${deployment_id}`} className="btn btn-outline" style={{ backgroundColor: 'transparent', border: '1px solid var(--border)', textDecoration: 'none', fontSize: '0.8125rem' }}>Exit</Link>
      </div>

      <div className="glass-card" style={{ padding: '1.25rem', display: 'flex', flexDirection: 'column', gap: '1rem', alignItems: 'center' }}>
        {previewUrl ? (
          <img src={previewUrl} alt="" style={{ maxWidth: '100%', maxHeight: 420, borderRadius: 'var(--radius)', background: 'var(--surface)' }} />
        ) : (
          // No rendition yet and a private original: the registry has no URL (#305).
          <div style={{ width: '100%', maxWidth: 560, aspectRatio: '4/3', borderRadius: 'var(--radius)', background: 'rgba(0,0,0,0.04)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <span style={{ fontSize: '2rem', opacity: 0.3 }}>📷</span>
          </div>
        )}

        {/* Score + reasons */}
        <div style={{ width: '100%' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.75rem', opacity: 0.8 }}>
            <span>AL score</span>
            <span>{current.active_learning_score != null ? Math.round(current.active_learning_score * 100) : '—'}%{reasons.length ? ` · ${reasons.join(', ')}` : ''}</span>
          </div>
          <div style={{ height: 5, background: 'rgba(255,255,255,0.1)', borderRadius: 3, overflow: 'hidden' }}>
            <div style={{ height: '100%', width: `${(current.active_learning_score ?? 0) * 100}%`, background: 'var(--primary)' }} />
          </div>
        </div>

        {/* AI label */}
        <div style={{ fontSize: '0.85rem', opacity: 0.85 }}>
          AI: <strong>{current.ai_label || 'unlabelled'}</strong>
          {current.ai_confidence != null && ` (${Math.round(current.ai_confidence * 100)}%)`}
        </div>

        {/* Species + actions */}
        <div style={{ display: 'flex', gap: 8, width: '100%', maxWidth: 480 }}>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Species (scientific name)…"
            style={{ flex: 1, padding: '0.5rem 0.75rem', borderRadius: 'var(--radius)', border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text-color)', fontSize: '0.875rem' }}
          />
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', justifyContent: 'center' }}>
          <button className="btn" disabled={decide.isPending || !name.trim()} onClick={() => submit('approve')} style={{ backgroundColor: 'var(--primary)' }}>✓ Approve (A)</button>
          <button className="btn" disabled={decide.isPending || !name.trim()} onClick={() => submit('reassign')} style={{ backgroundColor: 'transparent', border: '1px solid #ff9800', color: '#ff9800' }}>↻ Reassign (R)</button>
          <button className="btn btn-outline" disabled={decide.isPending} onClick={() => submit('expert')} style={{ backgroundColor: 'transparent', border: '1px solid var(--border)' }}>⚑ Expert (E)</button>
          <button className="btn btn-outline" disabled={decide.isPending} onClick={advance} style={{ backgroundColor: 'transparent', border: '1px solid var(--border)' }}>Skip (S)</button>
        </div>
      </div>
    </div>
  )
}
