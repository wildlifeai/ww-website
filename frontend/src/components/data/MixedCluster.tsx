// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// The mark on a Wildlife Brain group whose members carry more than one species, and the warning
// before confirming one as a single species (#450). Used on the Clusters page and by the grid's
// "Label all" when grouped by cluster; the words are in lib/clusterLabels.
import { TriangleAlert } from 'lucide-react'
import type { ClusterLabels } from '../../hooks/useBrain'
import { mixedTitle, warningText, type LabelChange } from '../../lib/clusterLabels'

const AMBER = '#f59e0b'

const BTN: React.CSSProperties = {
  fontSize: '0.75rem', padding: '0.25rem 0.6rem', borderRadius: 'var(--radius)', cursor: 'pointer', whiteSpace: 'nowrap',
}

export function MixedMark({ cluster }: { cluster: ClusterLabels }) {
  return (
    <span title={mixedTitle(cluster)}
      style={{ display: 'inline-flex', alignItems: 'center', gap: 3, fontSize: '0.68rem', padding: '1px 7px', borderRadius: 10, background: AMBER, color: '#fff', whiteSpace: 'nowrap' }}>
      <TriangleAlert size={11} aria-hidden /> Mixed species
    </span>
  )
}

interface WarningProps {
  changes: readonly LabelChange[]
  pickName: string
  pending: boolean
  onConfirm: () => void
  onCancel: () => void
}

export function MixedWarning({ changes, pickName, pending, onConfirm, onCancel }: WarningProps) {
  return (
    <div role="status" style={{
      display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap', padding: '0.5rem 0.75rem', fontSize: '0.8125rem',
      background: 'rgba(245,158,11,0.06)', border: '1px solid rgba(245,158,11,0.25)', borderRadius: 'var(--radius)',
    }}>
      <span style={{ flex: 1, minWidth: 180 }}>{warningText(changes, pickName)}</span>
      <button type="button" disabled={pending} onClick={onConfirm} style={{ ...BTN, background: AMBER, border: 'none', color: '#fff' }}>
        {pending ? 'Confirming…' : 'Confirm anyway'}
      </button>
      <button type="button" onClick={onCancel} style={{ ...BTN, background: 'transparent', border: '1px solid var(--border)', color: 'var(--text-color)' }}>
        Cancel
      </button>
    </div>
  )
}
