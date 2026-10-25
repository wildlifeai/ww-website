/**
 * SpeciesPicker: the shared taxon-validated species selector (AN-5).
 *
 * Offers the species picked recently and the species already labelled on the photos in view
 * (`seeds`) as soon as it opens, then searches the local `taxa` table, and asks iNaturalist only
 * when fewer than three local suggestions match (lib/speciesSuggestions). Selecting an iNat result
 * registers its full lineage into `taxa` (POST /api/inat/taxa) and returns the persisted row, so
 * callers write the taxon FK instead of an unvalidated string.
 *
 * Keyboard: arrow keys move, Enter picks the highlighted suggestion (the first by default), Esc
 * closes the list; a second Esc reaches the caller (the viewer closes its label bar).
 *
 * iNaturalist is feature-flagged on the backend; when disabled the search endpoint 404s and the
 * picker falls back to local results without an error.
 */
import { useState, useEffect, useCallback, useId, useMemo, useRef, useSyncExternalStore } from 'react'
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query'
import { supabase } from '../../config/supabase'
import { apiClient } from '../../lib/apiClient'
import {
  INAT_BELOW, recentSpecies, rememberSpecies, seedMatches, speciesKey, subscribeRecentSpecies,
  type SpeciesOption,
} from '../../lib/speciesSuggestions'

export interface SpeciesSelection {
  taxon_id: string | null
  scientific_name: string
  vernacular_name: string | null
}

type Source = 'recent' | 'labelled' | 'local' | 'inat'

interface Suggestion {
  /** Taxa row id for recent, labelled and local results; iNat numeric id (string) for remote ones. */
  id: string | null
  scientific_name: string
  common_name: string | null
  source: Source
}

interface Props {
  onSelect: (s: SpeciesSelection) => void
  placeholder?: string
  autoFocus?: boolean
  /** Shown inside the input before typing (e.g. the current species). */
  initialQuery?: string
  disabled?: boolean
  /** Species already labelled where the picker is used, offered before any search. */
  seeds?: SpeciesOption[]
}

const SOURCE_LABEL: Record<Source, string> = {
  recent: 'Recent', labelled: 'Labelled here', local: 'Species list', inat: 'iNaturalist',
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

const NO_SEEDS: SpeciesOption[] = []
const NO_SUGGESTIONS: Suggestion[] = []
const SEARCH_STALE_MS = 60_000

/** The local taxa table: authoritative and fast. */
async function searchTaxa(q: string): Promise<Suggestion[]> {
  const { data } = await supabase
    .from('taxa')
    .select('id, scientific_name, common_name')
    .or(`scientific_name.ilike.%${q}%,common_name.ilike.%${q}%`)
    .limit(8)
  return (data ?? []).map(t => ({ id: t.id, scientific_name: t.scientific_name, common_name: t.common_name, source: 'local' as const }))
}

/** iNaturalist autocomplete: best effort, may be disabled or offline. */
async function searchInat(q: string): Promise<Suggestion[]> {
  try {
    const res = await apiClient.get(`/api/inat/taxa/search?q=${encodeURIComponent(q)}`)
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const rows = ((res as any)?.data ?? []) as any[]
    return rows.filter(r => r?.name).slice(0, 6).map(r => ({
      id: String(r.id), scientific_name: r.name, common_name: r.preferred_common_name ?? null, source: 'inat' as const,
    }))
  } catch {
    return []
  }
}

/** Seeds, then taxa rows the seeds lack, then iNat names neither has. */
function merge(seeds: Suggestion[], taxa: Suggestion[], inat: Suggestion[]): Suggestion[] {
  const ids = new Set<string | null>(seeds.flatMap(s => (s.id ? [s.id] : [])))
  const local = [...seeds, ...taxa.filter(t => !ids.has(t.id))]
  const names = new Set(local.map(s => s.scientific_name.toLowerCase()))
  return [...local, ...inat.filter(s => !names.has(s.scientific_name.toLowerCase()))]
}

function seedSuggestions(q: string, recents: SpeciesOption[], labelled: SpeciesOption[]): Suggestion[] {
  const recentKeys = new Set(recents.map(speciesKey))
  return seedMatches(q, recents, labelled).map(s => ({
    id: s.taxon_id, scientific_name: s.scientific_name, common_name: s.vernacular_name,
    source: recentKeys.has(speciesKey(s)) ? 'recent' : 'labelled',
  }))
}

export function SpeciesPicker({ onSelect, placeholder, autoFocus, initialQuery = '', disabled, seeds = NO_SEEDS }: Props) {
  const listId = useId()
  const queryClient = useQueryClient()
  const recents = useSyncExternalStore(subscribeRecentSpecies, recentSpecies)
  const [query, setQuery] = useState(initialQuery)
  // The query as last searched: it follows `query` once typing pauses for 250 ms.
  const [term, setTerm] = useState('')
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const [registering, setRegistering] = useState(false)
  // Filter and search only once the user has typed, so an initialQuery does not pop results.
  const [touched, setTouched] = useState(false)
  // Set while an Enter waits for its search, so a second Enter does not pick twice.
  const enterPending = useRef(false)

  const q = touched ? query.trim() : ''
  useEffect(() => {
    if (!touched) return
    const t = setTimeout(() => setTerm(q), 250)
    return () => clearTimeout(t)
  }, [q, touched])

  const searching = q.length >= 2
  const seedHits = useMemo(() => seedSuggestions(q, recents, seeds), [q, recents, seeds])
  const taxa = useQuery({
    queryKey: ['species-taxa', term],
    queryFn: () => searchTaxa(term),
    enabled: searching && term.length >= 2,
    staleTime: SEARCH_STALE_MS,
    // Keep the last results on screen while the next search runs.
    placeholderData: keepPreviousData,
  })
  const taxaHits = searching ? (taxa.data ?? NO_SUGGESTIONS) : NO_SUGGESTIONS
  const localForTerm = merge(seedSuggestions(term, recents, seeds), taxa.isSuccess && !taxa.isPlaceholderData ? taxa.data : NO_SUGGESTIONS, []).length
  const inat = useQuery({
    queryKey: ['species-inat', term],
    queryFn: () => searchInat(term),
    enabled: searching && term.length >= 2 && taxa.isSuccess && !taxa.isPlaceholderData && localForTerm < INAT_BELOW,
    staleTime: SEARCH_STALE_MS,
  })
  const inatHits = searching && localForTerm < INAT_BELOW ? (inat.data ?? NO_SUGGESTIONS) : NO_SUGGESTIONS
  const suggestions = merge(seedHits, taxaHits, inatHits)
  const loading = searching && (term !== q || taxa.isFetching || inat.isFetching)
  const highlighted = Math.min(active, Math.max(0, suggestions.length - 1))

  const choose = useCallback(async (s: Suggestion) => {
    let picked: SpeciesSelection = { taxon_id: s.source === 'inat' ? null : s.id, scientific_name: s.scientific_name, vernacular_name: s.common_name }
    if (s.source === 'inat' && s.id) {
      // Remote: register the lineage into taxa, then return the persisted row. On failure keep the
      // raw name without a taxon_id rather than blocking the user.
      setRegistering(true)
      try {
        const res = await apiClient.post('/api/inat/taxa', { taxon_id: parseInt(s.id, 10) })
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const t = (res as any)?.data
        if (t?.id) picked = { taxon_id: t.id, scientific_name: t.scientific_name, vernacular_name: t.common_name ?? null }
      } catch {
        // keep the raw name
      } finally {
        setRegistering(false)
      }
    }
    rememberSpecies(picked)
    onSelect(picked)
    setQuery(picked.vernacular_name || picked.scientific_name)
    setTouched(false)
    setOpen(false)
  }, [onSelect])

  // Enter before the search has caught up with the typing: search the typed text now, then pick
  // its first result (iNaturalist only when nothing local matches).
  const chooseFirstFresh = async () => {
    enterPending.current = true
    try {
      const local = merge(seedHits, await queryClient.fetchQuery({
        queryKey: ['species-taxa', q], queryFn: () => searchTaxa(q), staleTime: SEARCH_STALE_MS,
      }), [])
      const first = local[0] ?? (await queryClient.fetchQuery({
        queryKey: ['species-inat', q], queryFn: () => searchInat(q), staleTime: SEARCH_STALE_MS,
      }))[0]
      if (first) await choose(first)
    } finally {
      enterPending.current = false
    }
  }

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      if (!open) setOpen(true)
      else setActive(Math.min(highlighted + 1, suggestions.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive(Math.max(highlighted - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      if (registering || enterPending.current) return
      // Recent and labelled species match the typed text already; only a server result can be stale.
      const stale = searching && (term !== q || taxa.isFetching) && highlighted >= seedHits.length
      if (stale) void chooseFirstFresh()
      else if (suggestions[highlighted]) void choose(suggestions[highlighted])
    } else if (e.key === 'Escape' && open) {
      // Close the list only; the next Esc goes to whoever holds the picker.
      e.stopPropagation()
      setOpen(false)
    }
  }

  const showList = open && !disabled && (suggestions.length > 0 || loading)
  const optionId = (i: number) => `${listId}-${i}`

  return (
    <div style={{ position: 'relative' }}>
      <input
        type="text"
        role="combobox"
        aria-expanded={showList}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={showList && suggestions[highlighted] ? optionId(highlighted) : undefined}
        value={query}
        autoFocus={autoFocus}
        disabled={disabled || registering}
        placeholder={registering ? 'Registering taxon…' : (placeholder ?? 'Search species…')}
        onChange={e => { setTouched(true); setQuery(e.target.value); setOpen(true); setActive(0) }}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={onKeyDown}
        style={INPUT}
      />
      {showList && (
        <div
          id={listId}
          role="listbox"
          // Keep the focus in the input while a suggestion is clicked.
          onMouseDown={e => e.preventDefault()}
          style={{
            position: 'absolute', top: '100%', left: 0, right: 0, marginTop: 2, zIndex: 50,
            backgroundColor: 'var(--surface)', border: '1px solid var(--border)',
            borderRadius: 'var(--radius)', maxHeight: 260, overflowY: 'auto',
            boxShadow: '0 6px 18px rgba(0,0,0,0.25)',
          }}
        >
          {suggestions.map((s, i) => (
            <div
              key={`${s.source}-${s.id ?? s.scientific_name}`}
              id={optionId(i)}
              role="option"
              aria-selected={i === highlighted}
              tabIndex={-1}
              onClick={() => void choose(s)}
              onMouseEnter={() => setActive(i)}
              style={{
                padding: '0.4rem 0.625rem', cursor: 'pointer', fontSize: '0.8125rem',
                display: 'flex', flexDirection: 'column', gap: 1,
                borderBottom: '1px solid var(--border)',
                backgroundColor: i === highlighted ? 'rgba(76,175,80,0.14)' : 'transparent',
              }}
            >
              <span style={{ fontWeight: 600 }}>{s.common_name || s.scientific_name}</span>
              <span style={{ fontSize: '0.6875rem', color: 'var(--text-muted)' }}>
                <i>{s.scientific_name}</i>, {SOURCE_LABEL[s.source]}
              </span>
            </div>
          ))}
          {loading && (
            <div style={{ padding: '0.5rem 0.625rem', fontSize: '0.75rem', color: 'var(--text-muted)' }}>Searching…</div>
          )}
        </div>
      )}
    </div>
  )
}
