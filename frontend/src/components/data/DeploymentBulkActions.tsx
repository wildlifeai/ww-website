// Copyright (c) 2024
// SPDX-License-Identifier: GPL-3.0-or-later
//
// DeploymentBulkActions: selection toolbar for the Insights > Deployments table.
// Replaces the per-row Actions column: the user ticks rows, then picks one action from this
// dropdown. Some actions only make sense for a single deployment (Show in Reports, Edit location,
// Move to project); those appear only when exactly one row is selected. The write actions appear
// only to a role that may do them on the selected rows' project (UI audit decision 1): Edit
// location to a project member, Move and Delete to a project admin. The table's CSV button
// exports the selection.
import { useState, useRef, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Camera, ChartColumn, ChevronDown, FolderInput, Map as MapIcon, MapPin, Trash2, type LucideIcon } from 'lucide-react'
import { useProjectSelection } from '../../hooks/useProjectSelection'
import { useProjectRoles } from '../../hooks/useProjectRoles'
import { apiClient } from '../../lib/apiClient'
import { showUndoToast } from '../common/undoToastBus'
import type { DeploymentRow } from './DeploymentActionRow'
import { EditLocationDialog } from './EditLocationDialog'
import { MoveDeploymentDialog } from './MoveDeploymentDialog'

interface Props {
  selected: Set<string>
  rows: DeploymentRow[]
  onClear: () => void
  onShowMap: () => void
  /** Called after a delete/undo so the parent can refetch the deployment list. */
  onDeleted?: () => void
  /** Called after a location edit was saved, so the parent can refetch the list. */
  onEdited?: () => void
  /** Called after a deployment moved to another project, so the parent can refetch the list. */
  onMoved?: () => void
}

const ITEM: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: '0.5rem', width: '100%', textAlign: 'left', padding: '0.45rem 0.875rem',
  fontSize: '0.8125rem', border: 'none', background: 'transparent', color: 'var(--text-color)', cursor: 'pointer',
}

function MenuItem({ icon: Icon, label, onClick, danger }: { icon: LucideIcon; label: string; onClick: () => void; danger?: boolean }) {
  return (
    <button style={danger ? { ...ITEM, color: 'var(--error, #f44336)' } : ITEM} onClick={onClick}>
      <Icon size={14} aria-hidden />{label}
    </button>
  )
}

export function DeploymentBulkActions({ selected, rows, onClear, onShowMap, onDeleted, onEdited, onMoved }: Props) {
  const navigate = useNavigate()
  const { clearAll, toggleProject } = useProjectSelection()
  const roles = useProjectRoles()
  const [open, setOpen] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [movingId, setMovingId] = useState<string | null>(null)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [open])

  if (selected.size === 0) return null

  const selectedRows = rows.filter(r => selected.has(r.id))
  const single = selectedRows.length === 1 ? selectedRows[0] : null
  const projectIds = [...new Set(selectedRows.map(r => r.project_id))]
  // Nothing role-dependent while the roles load. The creator rule for Edit location stays
  // server-side; a refusal comes back as the dialog's error.
  const canEdit = !!single && !roles.loading && roles.canWrite(single.project_id)
  const canMove = !!single && !roles.loading && roles.isProjectAdmin(single.project_id)
  const canDelete = !roles.loading && projectIds.length > 0 && projectIds.every(id => roles.isProjectAdmin(id))

  const run = (fn: () => void) => { setOpen(false); fn() }

  const openAnnotations = () => {
    if (single) { navigate(`/annotations?deployment=${single.id}`); return }
    clearAll()
    projectIds.forEach(pid => toggleProject(pid))
    navigate('/annotations')
  }

  const doDelete = async () => {
    const ids = selectedRows.map(r => r.id)
    setDeleting(true)
    setDeleteError(null)
    try {
      const res = await apiClient.del('/api/deployments/batch', { deployment_ids: ids }) as {
        deleted_at?: string; deployment_ids?: string[]; refused_ids?: string[]
      }
      const deletedAt = res?.deleted_at
      const deletedIds = res?.deployment_ids ?? ids
      const refused = res?.refused_ids?.length ?? 0
      setConfirming(false); setOpen(false); onClear(); onDeleted?.()
      if (deletedAt) {
        const skipped = refused
          ? `. ${refused} not deleted: only the person who set a deployment up, or a project admin, can delete it`
          : ''
        showUndoToast({
          message: `Deleted ${deletedIds.length} deployment${deletedIds.length !== 1 ? 's' : ''} (and their photos)${skipped}`,
          onUndo: async () => {
            await apiClient.post('/api/deployments/batch/restore', { deployment_ids: deletedIds, deleted_at: deletedAt })
            onDeleted?.()
          },
        })
      }
    } catch (e) {
      setConfirming(false); setOpen(false)
      setDeleteError(e instanceof Error ? e.message : 'Not deleted.')
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.75rem',
      padding: '0.5rem 0.75rem', borderRadius: 'var(--radius)',
      background: 'rgba(76,175,80,0.08)', border: '1px solid rgba(76,175,80,0.3)',
    }}>
      <span style={{ fontSize: '0.82rem', fontWeight: 600 }}>{selected.size} selected</span>
      <div ref={ref} style={{ position: 'relative' }}>
        <button
          className="btn"
          style={{ display: 'inline-flex', alignItems: 'center', gap: '0.25rem', fontSize: '0.8rem', padding: '0.3rem 0.7rem' }}
          onClick={() => { setOpen(v => !v); setConfirming(false); setDeleteError(null) }}
        >
          Actions <ChevronDown size={14} aria-hidden />
        </button>
        {open && (
          <div style={{
            position: 'absolute', top: 'calc(100% + 4px)', left: 0, zIndex: 50, minWidth: 220,
            background: 'var(--bg-color)', border: '1px solid var(--border)', borderRadius: 'var(--radius)',
            boxShadow: '0 4px 16px rgba(0,0,0,0.15)', padding: '0.25rem 0',
          }}>
            {confirming ? (
              <div style={{ padding: '0.6rem 0.875rem' }}>
                <div style={{ fontSize: '0.8rem', marginBottom: '0.5rem' }}>
                  Delete <strong>{selected.size}</strong> deployment{selected.size !== 1 ? 's' : ''}? This also removes
                  their photos and detections. You can undo.
                </div>
                <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end' }}>
                  <button style={{ ...ITEM, width: 'auto', padding: '0.3rem 0.7rem' }} onClick={() => setConfirming(false)}>Cancel</button>
                  <button
                    onClick={doDelete}
                    disabled={deleting}
                    style={{ padding: '0.3rem 0.8rem', fontSize: '0.8rem', border: 'none', borderRadius: 'var(--radius)', background: 'var(--error, #f44336)', color: '#fff', cursor: deleting ? 'wait' : 'pointer' }}
                  >
                    {deleting ? 'Deleting…' : 'Delete'}
                  </button>
                </div>
              </div>
            ) : (
              <>
                <MenuItem icon={Camera} label="Open in Media" onClick={() => run(openAnnotations)} />
                <MenuItem icon={MapIcon} label="Show on map" onClick={() => run(onShowMap)} />
                {single && (
                  <MenuItem icon={ChartColumn} label="Show in Reports"
                    onClick={() => run(() => navigate(`/insights?tab=reports&deployment=${single.id}`))} />
                )}
                {canEdit && (
                  <MenuItem icon={MapPin} label="Edit location" onClick={() => { setOpen(false); setEditingId(single.id) }} />
                )}
                {canMove && (
                  <MenuItem icon={FolderInput} label="Move to project" onClick={() => { setOpen(false); setMovingId(single.id) }} />
                )}
                {canDelete && (
                  <>
                    <div style={{ height: 1, background: 'var(--border)', margin: '0.25rem 0' }} />
                    <MenuItem icon={Trash2} label="Delete" danger onClick={() => setConfirming(true)} />
                  </>
                )}
              </>
            )}
          </div>
        )}
      </div>
      {deleteError && <span role="alert" style={{ fontSize: '0.8rem', color: 'var(--error, #ef4444)' }}>{deleteError}</span>}
      {editingId && (
        <EditLocationDialog
          deploymentId={editingId}
          onClose={changed => { setEditingId(null); if (changed) onEdited?.() }}
        />
      )}
      {movingId && (
        <MoveDeploymentDialog
          deploymentId={movingId}
          onClose={changed => { setMovingId(null); if (changed) onMoved?.() }}
        />
      )}
      <button onClick={() => { setDeleteError(null); onClear() }} style={{ marginLeft: 'auto', background: 'transparent', border: 'none', cursor: 'pointer', fontSize: '0.78rem', color: 'var(--text-muted)' }}>
        Clear
      </button>
    </div>
  )
}
