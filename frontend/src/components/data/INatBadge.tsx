/**
 * INatBadge: a small iNaturalist marker overlaid on a media thumbnail.
 *
 * The circle colour reflects the upload / community-ID state (from the
 * inat_observations.sync_status mapping); clicking opens the iNat observation.
 */
import { Bird } from 'lucide-react'

export type INatState =
  | 'pending'
  | 'uploaded'
  | 'needs_id'
  | 'research'
  | 'disagreement'
  | 'failed'

const COLOURS: Record<INatState, { bg: string; title: string }> = {
  pending:      { bg: '#9ca3af', title: 'Queued for iNaturalist upload' },
  uploaded:     { bg: '#74ac00', title: 'On iNaturalist, awaiting community ID' },
  needs_id:     { bg: '#74ac00', title: 'On iNaturalist, needs ID' },
  research:     { bg: '#16a34a', title: 'iNaturalist research grade, community confirmed' },
  disagreement: { bg: '#f59e0b', title: 'iNaturalist community taxon differs from your label' },
  failed:       { bg: '#ef4444', title: 'iNaturalist upload failed' },
}

export function INatBadge({ state, uri, size = 16 }: { state: INatState; uri?: string | null; size?: number }) {
  const c = COLOURS[state] ?? COLOURS.uploaded
  const dot = (
    <span
      title={c.title}
      style={{
        width: size, height: size, borderRadius: '50%', backgroundColor: c.bg,
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        lineHeight: 1, color: '#fff',
        boxShadow: '0 0 0 1.5px rgba(255,255,255,0.85)',
      }}
    >
      <Bird size={Math.round(size * 0.65)} aria-hidden />
    </span>
  )
  if (uri) {
    return (
      <a href={uri} target="_blank" rel="noreferrer" onClick={e => e.stopPropagation()} style={{ textDecoration: 'none' }}>
        {dot}
      </a>
    )
  }
  return dot
}
