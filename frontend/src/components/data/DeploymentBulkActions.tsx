// Copyright (c) 2024
// SPDX-License-Identifier: GPL-3.0-or-later
//
// DeploymentBulkActions — selection toolbar for the Insights ▸ Deployments table.
// Replaces the per-row Actions column: the user ticks rows, then picks one action from
// this dropdown. Some actions only make sense for a single deployment (Results, Edit location);
// those appear only when exactly one row is selected.
import { useState, useRef, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useProjectSelection } from '../../hooks/useProjectSelection'
import { apiClient } from '../../lib/apiClient'
import { useDemoGuard } from '../common/DemoGuard'
import { showUndoToast } from '../common/undoToastBus'
import type { DeploymentRow } from './DeploymentActionRow'
import { EditLocationDialog } from './EditLocationDialog'

interface Props {
  selected: Set<string>
  rows: DeploymentRow[]
  onClear: () => void
  onShowMap: () => void
  /** Called after a delete/undo so the parent can refetch the deployment list. */
  onDeleted?: () => void
  /** Called after a location edit was saved, so the parent can refetch the list. */
  onEdited?: () => void
}

function downloadCsv(rows: DeploymentRow[]) {
  const cols: (keyof DeploymentRow)[] = ['project_name', 'device_name', 'location_name', 'latitude', 'longitude', 'deployment_start', 'deployment_end']
  const esc = (v: unknown) => `"${String(v ?? '').replace(/"/g, '""')}"`
  const csv = [cols.join(','), ...rows.map(r => cols.map(c => esc((r as any)[c])).join(','))].join('\n')
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }))
  const a = document.createElement('a')
  a.href = url; a.download = `deployments-selected-${new Date().toISOString().slice(0, 10)}.csv`; a.click()
  URL.revokeObjectURL(url)
}

export function DeploymentBulkActions({ selected, rows, onClear, onShowMap, onDeleted, onEdited }: Props) {
  const navigate = useNavigate()
  const { clearAll, toggleProject } = useProjectSelection()
  const { guard } = useDemoGuard()
  const [open, setOpen] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
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

  const item: React.CSSProperties = {
    display: 'block', width: '100%', textAlign: 'left', padding: '0.45rem 0.875rem',
    fontSize: '0.8125rem', border: 'none', background: 'transparent', color: 'var(--text-color)', cursor: 'pointer',
  }
  const run = (fn: () => void) => { setOpen(false); fn() }

  const openAnnotations = () => {
    if (single) { navigate(`/annotations?deployment=${single.id}`); return }
    clearAll()
    Array.from(new Set(selectedRows.map(r => r.project_id))).forEach(pid => toggleProject(pid))
    navigate('/annotations')
  }

  const doDelete = async () => {
    const ids = selectedRows.map(r => r.id)
    setDeleting(true)
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
      alert(e instanceof Error ? e.message : 'Delete failed — you may not have permission.')
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
        <button className="btn" style={{ fontSize: '0.8rem', padding: '0.3rem 0.7rem' }} onClick={() => { setOpen(v => !v); setConfirming(false) }}>
          Actions ▾
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
                  <button style={{ ...item, width: 'auto', padding: '0.3rem 0.7rem' }} onClick={() => setConfirming(false)}>Cancel</button>
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
                <button style={item} onClick={() => run(openAnnotations)}>🏷️ Open in Annotations</button>
                <button style={item} onClick={() => run(onShowMap)}>🗺 Show on map</button>
                <button style={item} onClick={() => run(() => downloadCsv(selectedRows))}>📄 Export selected (CSV)</button>
                {single && (
                  <button style={item} onClick={() => run(() => navigate(`/reporting/${single.id}`))}>📊 Results</button>
                )}
                {single && (
                  <button style={item} onClick={guard(() => { setOpen(false); setEditingId(single.id) })}>📍 Edit location</button>
                )}
                <div style={{ height: 1, background: 'var(--border)', margin: '0.25rem 0' }} />
                <button style={{ ...item, color: 'var(--error, #f44336)' }} onClick={guard(() => setConfirming(true))}>🗑 Delete</button>
              </>
            )}
          </div>
        )}
      </div>
      {editingId && (
        <EditLocationDialog
          deploymentId={editingId}
          onClose={changed => { setEditingId(null); if (changed) onEdited?.() }}
        />
      )}
      <button onClick={onClear} style={{ marginLeft: 'auto', background: 'transparent', border: 'none', cursor: 'pointer', fontSize: '0.78rem', opacity: 0.65 }}>
        Clear
      </button>
    </div>
  )
}
