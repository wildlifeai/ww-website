// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// ClusterLabelAll: the per-cluster header action when the review grid is grouped by cluster.
// Labels every photo in the cluster as one species in a single call (the "confirm cluster"
// workflow). The species comes from SpeciesPicker, so the label carries a taxon_id (UI audit
// 02 #26). On a group that mixes species it warns first, saying which labels change (#450). The
// grid shows it only to people who can write to the deployment's project.
import { useState } from 'react'
import { Tags, X } from 'lucide-react'
import { useConfirmCluster, type ClusterLabels } from '../../hooks/useBrain'
import { confirmWarning } from '../../lib/clusterLabels'
import { MixedWarning } from './MixedCluster'
import { SpeciesPicker, type SpeciesSelection } from './SpeciesPicker'

interface Props {
  clusterId: string
  deploymentId: string
  /** The deployment's project, whose species the picker suggests. */
  projectId?: string
  /** The species the cluster's members carry; unset while loading or when the read failed. */
  labels?: ClusterLabels
  labelsLoading?: boolean
  onDone: (created: number, name: string) => void
}

const SMALL: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', gap: '0.3rem', fontSize: '0.72rem', padding: '0.2rem 0.5rem',
  border: '1px solid var(--border)', borderRadius: 'var(--radius)', background: 'transparent', cursor: 'pointer', whiteSpace: 'nowrap',
}

const nameOf = (s: SpeciesSelection) => s.vernacular_name || s.scientific_name

export function ClusterLabelAll({ clusterId, deploymentId, projectId, labels, labelsLoading, onDone }: Props) {
  const [open, setOpen] = useState(false)
  // The species picked for a mixed group, while the warning asks first.
  const [asking, setAsking] = useState<SpeciesSelection | null>(null)
  const confirm = useConfirmCluster(deploymentId)
  const changes = asking ? confirmWarning(labels, asking) : null

  const run = (s: SpeciesSelection) => {
    confirm.mutate(
      { id: clusterId, taxon: { taxon_id: s.taxon_id ?? undefined, scientific_name: s.scientific_name, vernacular_name: s.vernacular_name ?? undefined } },
      {
        onSuccess: r => { setOpen(false); setAsking(null); onDone(r?.observations_created ?? 0, nameOf(s)) },
        // Back to the picker, which says "Not saved".
        onError: () => setAsking(null),
      },
    )
  }

  const apply = (s: SpeciesSelection) => {
    if (confirmWarning(labels, s)) setAsking(s)
    else run(s)
  }

  if (!open) {
    return (
      <button type="button" onClick={() => setOpen(true)} title="Label every photo in this cluster as one species"
        style={{ ...SMALL, color: 'var(--primary)' }}>
        <Tags size={13} aria-hidden /> Label all
      </button>
    )
  }
  if (asking && changes) {
    return <MixedWarning changes={changes} pickName={nameOf(asking)} pending={confirm.isPending} onConfirm={() => run(asking)} onCancel={() => setAsking(null)} />
  }
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem' }}>
      <span style={{ width: 220 }}>
        <SpeciesPicker autoFocus disabled={confirm.isPending || labelsLoading} placeholder="Label all as…" onSelect={apply}
          projectIds={projectId ? [projectId] : undefined} />
      </span>
      <button type="button" onClick={() => setOpen(false)} aria-label="Cancel"
        style={{ ...SMALL, border: 'none', color: 'var(--text-muted)' }}>
        <X size={14} aria-hidden />
      </button>
      {confirm.isError && <span style={{ fontSize: '0.7rem', color: 'var(--error)' }}>Not saved</span>}
    </span>
  )
}
