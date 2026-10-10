// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// MoveDeploymentDialog: move one deployment to another project from Insights > Deployments (#288).
// The picker lists only the projects the move could succeed into (lib/moveDeployment), the text
// under it says what goes along, and the move runs as the user through the move_deployment RPC,
// which re-checks the rule and whose refusals come back as plain messages.
import { useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Modal } from '../ui/Modal'
import { supabase } from '../../config/supabase'
import { useAuth } from '../../hooks/useAuth'
import { fetchMoveContext, moveDeployment, type MoveContext } from '../../lib/moveDeployment'

interface Props {
  deploymentId: string
  /** `changed` is true when the deployment moved, so the caller can refetch its list. */
  onClose: (changed: boolean) => void
}

const LABEL: React.CSSProperties = { display: 'flex', flexDirection: 'column', gap: '0.25rem', fontSize: '0.8125rem', fontWeight: 500 }
const SELECT: React.CSSProperties = {
  width: '100%', padding: '0.4rem 0.55rem', borderRadius: 'var(--radius)', border: '1px solid var(--border)',
  backgroundColor: 'var(--surface)', color: 'var(--text-color)', fontSize: '0.8125rem', boxSizing: 'border-box',
}
const TEXT: React.CSSProperties = { fontSize: '0.8125rem', margin: 0 }
const NOTE: React.CSSProperties = { fontSize: '0.75rem', opacity: 0.7, margin: 0 }
const ERROR: React.CSSProperties = { fontSize: '0.75rem', color: 'var(--error, #ef4444)' }
const BTN: React.CSSProperties = {
  padding: '0.4rem 0.9rem', fontSize: '0.8125rem', borderRadius: 'var(--radius)', cursor: 'pointer',
  border: '1px solid var(--border)', background: 'transparent', color: 'var(--text-color)',
}

export function MoveDeploymentDialog({ deploymentId, onClose }: Props) {
  const { user } = useAuth()
  const userId = user?.id
  // Read only when the dialog closes, so a ref rather than state.
  const moved = useRef(false)
  const query = useQuery({
    queryKey: ['move-deployment-context', deploymentId, userId],
    queryFn: () => fetchMoveContext(supabase, userId as string, deploymentId),
    enabled: !!userId,
    staleTime: 0,
    gcTime: 0,
  })
  const close = () => onClose(moved.current)

  let body: React.ReactNode
  if (query.isPending) body = <p style={{ opacity: 0.5 }}>Loading…</p>
  else if (query.isError) body = <p style={ERROR}>{query.error.message}</p>
  else if (!query.data) body = <p style={{ opacity: 0.7 }}>This deployment is not available to you.</p>
  else body = <MoveForm context={query.data} onMoved={() => { moved.current = true }} onClose={close} />

  return <Modal open onClose={close} title="Move to project" size="sm">{body}</Modal>
}

function MoveForm({ context, onMoved, onClose }: {
  context: MoveContext
  onMoved: () => void
  onClose: () => void
}) {
  const { source, targets } = context
  const [targetId, setTargetId] = useState('')
  const [moving, setMoving] = useState(false)
  const [movedTo, setMovedTo] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const target = targets.find(t => t.id === targetId)
  const name = context.locationName || 'This deployment'

  const closeButton = (
    <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
      <button type="button" onClick={onClose} style={BTN}>Close</button>
    </div>
  )

  if (movedTo) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.85rem' }}>
        <p style={{ ...TEXT, color: 'var(--success, #10b981)' }}>{movedTo} ✓</p>
        {closeButton}
      </div>
    )
  }

  if (!context.canMoveFrom || targets.length === 0) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.85rem' }}>
        <p style={TEXT}>
          {context.canMoveFrom
            ? <>There is no project you can move it to. The target must be another project in the same organisation as <strong>{source.name}</strong>, not archived, and you must be one of its admins.</>
            : <>Only an admin of <strong>{source.name}</strong> can move its deployments, and only into another project they also administer.</>}
        </p>
        {closeButton}
      </div>
    )
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!target) return
    setMoving(true)
    setError(null)
    try {
      const result = await moveDeployment(supabase, context.deploymentId, target.id)
      setMovedTo(result.moved ? `Moved to ${target.name}.` : `It was already in ${target.name}.`)
      onMoved()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Not moved.')
    } finally {
      setMoving(false)
    }
  }

  return (
    <form onSubmit={submit} style={{ display: 'flex', flexDirection: 'column', gap: '0.85rem' }}>
      <p style={TEXT}><strong>{name}</strong> is in <strong>{source.name}</strong>.</p>
      <label style={LABEL}>
        Move to
        <select value={targetId} onChange={e => setTargetId(e.target.value)} style={SELECT}>
          <option value="">Choose a project</option>
          {targets.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
        </select>
      </label>
      {target && (
        <>
          <p style={TEXT}>
            Its photos, observations, annotations and alerts move with it. People who see it only
            through <strong>{source.name}</strong> will no longer see it.
          </p>
          <p style={NOTE}>Originals already in Google Drive stay in the {source.name} folder; new uploads go to {target.name}.</p>
        </>
      )}

      {error && <div style={ERROR}>{error}</div>}

      <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end' }}>
        <button type="button" onClick={onClose} style={BTN}>Cancel</button>
        <button type="submit" className="btn" disabled={!target || moving} style={{ fontSize: '0.8125rem', padding: '0.4rem 0.9rem', opacity: !target || moving ? 0.6 : 1 }}>
          {moving ? 'Moving…' : 'Move'}
        </button>
      </div>
    </form>
  )
}
