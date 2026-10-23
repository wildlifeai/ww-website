// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// ClusterLabelAll: the per-cluster header action when the review grid is grouped by cluster.
// Labels every photo in the cluster as one species in a single call (the "confirm cluster"
// workflow). The species comes from SpeciesPicker, so the label carries a taxon_id (UI audit
// 02 #26). The grid shows it only to people who can write to the deployment's project.
import { useState } from 'react'
import { Tags, X } from 'lucide-react'
import { useConfirmCluster } from '../../hooks/useBrain'
import { SpeciesPicker, type SpeciesSelection } from './SpeciesPicker'

interface Props {
  clusterId: string
  deploymentId: string
  onDone: (created: number, name: string) => void
}

const SMALL: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', gap: '0.3rem', fontSize: '0.72rem', padding: '0.2rem 0.5rem',
  border: '1px solid var(--border)', borderRadius: 'var(--radius)', background: 'transparent', cursor: 'pointer', whiteSpace: 'nowrap',
}

export function ClusterLabelAll({ clusterId, deploymentId, onDone }: Props) {
  const [open, setOpen] = useState(false)
  const confirm = useConfirmCluster(deploymentId)

  const apply = (s: SpeciesSelection) => {
    confirm.mutate(
      { id: clusterId, taxon: { taxon_id: s.taxon_id ?? undefined, scientific_name: s.scientific_name, vernacular_name: s.vernacular_name ?? undefined } },
      { onSuccess: r => { setOpen(false); onDone(r?.observations_created ?? 0, s.vernacular_name || s.scientific_name) } },
    )
  }

  if (!open) {
    return (
      <button type="button" onClick={() => setOpen(true)} title="Label every photo in this cluster as one species"
        style={{ ...SMALL, color: 'var(--primary)' }}>
        <Tags size={13} aria-hidden /> Label all
      </button>
    )
  }
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem' }}>
      <span style={{ width: 220 }}>
        <SpeciesPicker autoFocus disabled={confirm.isPending} placeholder="Label all as…" onSelect={apply} />
      </span>
      <button type="button" onClick={() => setOpen(false)} aria-label="Cancel"
        style={{ ...SMALL, border: 'none', color: 'var(--text-muted)' }}>
        <X size={14} aria-hidden />
      </button>
      {confirm.isError && <span style={{ fontSize: '0.7rem', color: 'var(--error)' }}>Not saved</span>}
    </span>
  )
}
