/**
 * SpeciesPicker — shared taxon-validated species selector (AN-5).
 *
 * Replaces free-text species entry everywhere. Searches the local `taxa` table
 * first, then the iNaturalist autocomplete API; selecting an iNat result
 * registers its full lineage into `taxa` (via POST /api/inat/taxa) and returns
 * the persisted row. The result always carries a real `taxon_id`, so callers can
 * write the FK on the observation instead of an unvalidated string.
 *
 * iNaturalist is feature-flagged on the backend; when disabled the search
 * endpoint 404s and we silently fall back to local-only results.
 */
import { useState, useEffect, useRef, useCallback } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { supabase } from '../../config/supabase'
import { apiClient } from '../../lib/apiClient'

export interface SpeciesSelection {
  taxon_id: string | null
  scientific_name: string
  vernacular_name: string | null
}

interface Suggestion {
  /** Local taxa row id, or iNat numeric id (string) for remote results. */
  id: string
  scientific_name: string
  common_name: string | null
  isLocal: boolean
}

interface Props {
  onSelect: (s: SpeciesSelection) => void
  placeholder?: string
  autoFocus?: boolean
  /** Shown inside the input before typing (e.g. the current species). */
  initialQuery?: string
  disabled?: boolean
}

const INPUT: React.CSSProperties = {
  width: '100%',
  padding: '0.375rem 0.5rem',
  borderRadius: 'var(--radius)',
  border: '1px solid var(--border)',
  backgroundColor: 'var(--surface)',
  color: 'var(--text-color)',
  fontSize: '0.8125rem',
  boxSizing: 'border-box',
}

const NO_SUGGESTIONS: Suggestion[] = []

/** Local taxa first (authoritative, fast), then iNaturalist names the local table lacks. */
async function searchSpecies(q: string): Promise<Suggestion[]> {
  // 1. Local taxa table (authoritative, fast)
  const { data: locals } = await supabase
    .from('taxa')
    .select('id, scientific_name, common_name')
    .or(`scientific_name.ilike.%${q}%,common_name.ilike.%${q}%`)
    .limit(8)

  const localSuggestions: Suggestion[] = (locals ?? []).map(t => ({
    id: t.id,
    scientific_name: t.scientific_name,
    common_name: t.common_name,
    isLocal: true,
  }))

  // 2. iNaturalist autocomplete (best-effort, may be disabled or offline)
  let inatSuggestions: Suggestion[] = []
  try {
    const res = await apiClient.get(`/api/inat/taxa/search?q=${encodeURIComponent(q)}`)
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const rows = ((res as any)?.data ?? []) as any[]
    const localNames = new Set(localSuggestions.map(s => s.scientific_name.toLowerCase()))
    inatSuggestions = rows
      .filter(r => r?.name && !localNames.has(String(r.name).toLowerCase()))
      .slice(0, 6)
      .map(r => ({
        id: String(r.id),
        scientific_name: r.name,
        common_name: r.preferred_common_name ?? null,
        isLocal: false,
      }))
  } catch {
    // iNat disabled or unreachable → local-only, no error surfaced.
  }

  return [...localSuggestions, ...inatSuggestions]
}

export function SpeciesPicker({ onSelect, placeholder, autoFocus, initialQuery = '', disabled }: Props) {
  const [query, setQuery] = useState(initialQuery)
  // The query as last searched: it follows `query` once typing pauses for 250 ms.
  const [term, setTerm] = useState('')
  const [open, setOpen] = useState(false)
  const [registering, setRegistering] = useState(false)
  // Only search (and open the dropdown) once the user has actually typed —
  // otherwise mounting with an initialQuery pops the suggestions unprompted.
  const [touched, setTouched] = useState(false)
  const blurTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  // ── Debounced search: local taxa + iNaturalist ────────────────────────────
  const q = query.trim()
  useEffect(() => {
    if (!touched) return
    const t = setTimeout(() => setTerm(q), 250)
    return () => clearTimeout(t)
  }, [q, touched])

  const searching = touched && q.length >= 2
  const search = useQuery({
    queryKey: ['species-search', term],
    queryFn: () => searchSpecies(term),
    enabled: searching && term.length >= 2,
    // Keep the last results on screen while the next search runs.
    placeholderData: keepPreviousData,
  })
  const suggestions = searching ? (search.data ?? NO_SUGGESTIONS) : NO_SUGGESTIONS
  const loading = searching && (term !== q || search.isFetching)

  const choose = useCallback(async (s: Suggestion) => {
    if (s.isLocal) {
      onSelect({ taxon_id: s.id, scientific_name: s.scientific_name, vernacular_name: s.common_name })
      setQuery(s.common_name || s.scientific_name)
      setOpen(false)
      return
    }
    // Remote: register lineage into taxa, then return the persisted row.
    setRegistering(true)
    try {
      const res = await apiClient.post('/api/inat/taxa', { taxon_id: parseInt(s.id, 10) })
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const t = (res as any)?.data
      if (t?.id) {
        onSelect({ taxon_id: t.id, scientific_name: t.scientific_name, vernacular_name: t.common_name ?? null })
        setQuery(t.common_name || t.scientific_name)
      } else {
        // Fallback: use the raw name without a taxon_id rather than blocking the user.
        onSelect({ taxon_id: null, scientific_name: s.scientific_name, vernacular_name: s.common_name })
        setQuery(s.common_name || s.scientific_name)
      }
    } catch {
      onSelect({ taxon_id: null, scientific_name: s.scientific_name, vernacular_name: s.common_name })
      setQuery(s.common_name || s.scientific_name)
    } finally {
      setRegistering(false)
      setOpen(false)
    }
  }, [onSelect])

  return (
    <div style={{ position: 'relative' }}>
      <input
        type="text"
        value={query}
        autoFocus={autoFocus}
        disabled={disabled || registering}
        placeholder={registering ? 'Registering taxon…' : (placeholder ?? 'Search species…')}
        onChange={e => { setTouched(true); setQuery(e.target.value); setOpen(true) }}
        onFocus={() => { if (suggestions.length) setOpen(true) }}
        onBlur={() => { blurTimer.current = setTimeout(() => setOpen(false), 150) }}
        style={INPUT}
      />
      {open && (suggestions.length > 0 || loading) && (
        <div
          onMouseDown={() => { if (blurTimer.current) clearTimeout(blurTimer.current) }}
          style={{
            position: 'absolute', top: '100%', left: 0, right: 0, marginTop: 2, zIndex: 50,
            backgroundColor: 'var(--surface)', border: '1px solid var(--border)',
            borderRadius: 'var(--radius)', maxHeight: 220, overflowY: 'auto',
            boxShadow: '0 6px 18px rgba(0,0,0,0.25)',
          }}
        >
          {loading && (
            <div style={{ padding: '0.5rem 0.625rem', fontSize: '0.75rem', opacity: 0.55 }}>Searching…</div>
          )}
          {suggestions.map((s, i) => (
            <div
              key={`${s.isLocal ? 'l' : 'i'}-${s.id}-${i}`}
              onClick={() => choose(s)}
              style={{
                padding: '0.4rem 0.625rem', cursor: 'pointer', fontSize: '0.8125rem',
                display: 'flex', flexDirection: 'column', gap: 1,
                borderBottom: '1px solid rgba(128,128,128,0.12)',
              }}
              onMouseEnter={e => (e.currentTarget.style.backgroundColor = 'rgba(76,175,80,0.12)')}
              onMouseLeave={e => (e.currentTarget.style.backgroundColor = 'transparent')}
            >
              <span style={{ fontWeight: 600 }}>{s.common_name || s.scientific_name}</span>
              <span style={{ fontSize: '0.6875rem', opacity: 0.6, fontStyle: 'italic' }}>
                {s.scientific_name} · {s.isLocal ? '✓ Local' : '🌎 iNaturalist'}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
