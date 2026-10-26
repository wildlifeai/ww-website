// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// GridBulkDialogs: the dialogs the review grid's Actions menu opens (hooks/useGridBulkActions).
import type { GridBulkActions } from '../../hooks/useGridBulkActions'
import type { MediaRecord } from '../../lib/gridMedia'
import { labelledSpecies } from '../../lib/speciesSuggestions'
import { DeleteConfirmModal, AiModelPickerModal, PipelineLogModal } from './BulkActionModals'
import { BulkLabelModal } from './BulkLabelModal'
import { TrainModelModal } from './TrainModelModal'

interface Props {
  bulk: GridBulkActions
  selectedIds: ReadonlySet<string>
  /** The cards on screen: the page, or the similar list. */
  visible: readonly MediaRecord[]
  /** The page's own rows, whose labels seed the species picker. */
  media: readonly MediaRecord[]
  deployments: readonly { id: string; project_id: string }[]
}

export function GridBulkDialogs({ bulk, selectedIds, visible, media, deployments }: Props) {
  const selected = visible.filter(m => selectedIds.has(m.id))
  const projectOf = new Map(deployments.map(d => [d.id, d.project_id]))
  const last = bulk.pipelineLogs?.[bulk.pipelineLogs.length - 1]
  return (
    <>
      {bulk.dialog === 'delete' && (
        <DeleteConfirmModal
          mediaIds={Array.from(selectedIds)}
          fileNames={selected.map(m => m.file_name || m.file_path.split('/').pop() || m.id)}
          onConfirm={bulk.deleteSelected}
          onClose={bulk.closeDialog}
        />
      )}
      {bulk.dialog === 'ai' && (
        <AiModelPickerModal count={selectedIds.size} onRun={bulk.runAi} onClose={bulk.closeDialog} />
      )}
      {bulk.dialog === 'label' && (
        <BulkLabelModal
          count={selectedIds.size}
          busy={bulk.labelBusy}
          onApply={bulk.label}
          onClose={bulk.closeDialog}
          seeds={labelledSpecies(media.flatMap(m => m.observations))}
          projectIds={[...new Set(selected.flatMap(m => projectOf.get(m.deployment_id) ?? []))]}
        />
      )}
      {bulk.dialog === 'train' && (() => {
        // The selection as the trainer will see it (optimistic upload placeholders are not rows yet).
        const rows = selected.filter(m => !m._pending)
        const depIds = new Set(rows.map(m => m.deployment_id))
        const projectIds = [...new Set(deployments.filter(d => depIds.has(d.id)).map(d => d.project_id))]
        return <TrainModelModal media={rows} projectIds={projectIds} onClose={bulk.closeDialog} />
      })()}
      {bulk.pipelineLogs !== null && (
        <PipelineLogModal
          isRunning={bulk.pipelineLogs.length > 0 && !last?.startsWith('✓') && !last?.startsWith('⚠')}
          logs={bulk.pipelineLogs}
          onClose={bulk.closeLogs}
        />
      )}
    </>
  )
}
