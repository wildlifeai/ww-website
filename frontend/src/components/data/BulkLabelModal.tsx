/**
 * BulkLabelModal: give every selected photo one label.
 *
 * Pick a species (search; Enter takes the first suggestion) or "Nothing (blank)". The write lives
 * in MediaBrowser, which has the media and deployment context; this component only collects the
 * choice. AI labels stay as they are (UI audit decision 2, lib/humanVerdict.ts).
 */
import { Ban } from 'lucide-react'
import { Modal } from '../ui/Modal'
import { SpeciesPicker, type SpeciesSelection } from './SpeciesPicker'
import type { SpeciesOption } from '../../lib/speciesSuggestions'

interface Props {
  count: number
  busy: boolean
  /** species selection, or null for "nothing" (blank). */
  onApply: (selection: SpeciesSelection | null) => void
  onClose: () => void
  /** Species already labelled on the photos in view, offered before any search. */
  seeds?: SpeciesOption[]
}

export function BulkLabelModal({ count, busy, onApply, onClose, seeds }: Props) {
  const photos = `${count} photo${count !== 1 ? 's' : ''}`
  return (
    <Modal open={true} onClose={onClose} title={`Label ${photos}`}>
      <p style={{ fontSize: '0.8125rem', color: 'var(--text-muted)', marginTop: 0 }}>
        Gives each selected photo your label. AI labels stay as they are.
      </p>

      <div style={{ marginBottom: '0.75rem' }}>
        <div style={{ fontSize: '0.78rem', fontWeight: 600, marginBottom: '0.35rem' }}>Species</div>
        <SpeciesPicker
          autoFocus
          seeds={seeds}
          placeholder="Search species (e.g. Eurasian blackbird)…"
          disabled={busy}
          onSelect={(s) => onApply(s)}
        />
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginTop: '1rem' }}>
        <button
          className="btn btn-outline"
          disabled={busy}
          onClick={() => onApply(null)}
          style={{ display: 'inline-flex', alignItems: 'center', gap: '0.35rem', backgroundColor: 'transparent', border: '1px solid var(--border)', fontSize: '0.8rem' }}
          title={`Mark ${photos} as empty, no animal`}
        >
          <Ban size={14} aria-hidden /> Nothing (blank)
        </button>
        <button
          onClick={onClose}
          disabled={busy}
          style={{ marginLeft: 'auto', background: 'none', border: 'none', cursor: 'pointer', fontSize: '0.8rem', color: 'var(--text-muted)' }}
        >
          Cancel
        </button>
      </div>

      {busy && <p style={{ fontSize: '0.78rem', color: 'var(--text-muted)', marginBottom: 0 }}>Applying…</p>}
    </Modal>
  )
}
