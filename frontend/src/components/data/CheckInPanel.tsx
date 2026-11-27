// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// CheckInPanel: a picked camera's check-in period on the Realtime page, read or changed over
// LoRaWAN (#521, lib/lorawanDownlinks.ts). FieldPage shows it only to a project admin of the
// camera's project (or a ww_admin) and only for an open deployment. The camera's newest request
// and newest reply are one cached read under realtimeKeys.checkIn, which useFieldLive reads again
// when either changes, after a reconnect and on return to the tab.
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { supabase } from '../../config/supabase'
import { silenceSpan } from '../../lib/cameraState'
import { feedTime } from '../../lib/lorawanFeed'
import { formatPing, LORAWAN_PING_MINUTES, PING_CHOICES } from '../../lib/lorawanSettings'
import {
  canRequest, fetchCheckInState, knownPeriod, requestDownlink, requestText,
} from '../../lib/lorawanDownlinks'
import { realtimeKeys } from '../../lib/realtimeReads'

const MUTED: React.CSSProperties = { color: 'var(--text-muted)' }
const SELECT: React.CSSProperties = {
  padding: '0.3rem 0.45rem', fontSize: '0.8rem', border: '1px solid var(--border)', borderRadius: 'var(--radius)',
  background: 'var(--surface)', color: 'var(--text-color)',
}

export function CheckInPanel({ deploymentId, name, pingMinutes }: {
  deploymentId: string
  /** The camera's name, as on its label. */
  name: string
  /** The deployment's check-in period: 0 LoRaWAN off, null not recorded. */
  pingMinutes: number | null
}) {
  const queryClient = useQueryClient()
  const key = realtimeKeys.checkIn(deploymentId)
  const query = useQuery({ queryKey: key, queryFn: () => fetchCheckInState(supabase, deploymentId) })
  const [choice, setChoice] = useState<number | null>(null)
  const [sending, setSending] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)

  const request = query.data?.request ?? null
  const reply = query.data?.reply ?? null
  const known = knownPeriod(reply, pingMinutes || null)
  const picked = choice ?? (known && PING_CHOICES.includes(known.minutes) ? known.minutes : LORAWAN_PING_MINUTES.default)
  const off = query.isPending || sending || !canRequest(request)
  const button: React.CSSProperties = {
    padding: '0.3rem 0.75rem', fontSize: '0.8125rem', opacity: off ? 0.5 : 1, cursor: off ? 'not-allowed' : 'pointer',
  }

  // The buttons stay off until the request row is read back, so it cannot be sent twice.
  const send = async (req: { kind: 'op_get' } | { kind: 'op_set'; value: number }) => {
    setSending(true)
    setRefusal(null)
    try {
      const result = await requestDownlink(supabase, deploymentId, req)
      if (!result.ok) setRefusal(result.text)
      await queryClient.invalidateQueries({ queryKey: key })
    } catch (e) {
      setRefusal(e instanceof Error ? e.message : String(e))
    } finally {
      setSending(false)
    }
  }

  const replyMinutes = request && reply && reply.messageId === request.reply_message_id ? reply.minutes : null
  const status = refusal ?? (request ? requestText(request, pingMinutes || null, replyMinutes, feedTime) : null)

  return (
    <div className="card" style={{ padding: '0.9rem 1.1rem', marginBottom: '1.25rem', fontSize: '0.8125rem' }}>
      <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'baseline', marginBottom: '0.5rem' }}>
        <strong>{name}</strong>
        <span>
          Check-in period: {known ? `every ${formatPing(known.minutes)}` : 'not known'}
        </span>
        {known && (
          <span style={MUTED}>
            {known.from === 'camera' && known.at ? `the camera said, ${feedTime(known.at)}` : 'as deployed'}
          </span>
        )}
      </div>
      <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'center' }}>
        <button type="button" className="btn btn-outline" disabled={off} style={button} onClick={() => void send({ kind: 'op_get' })}>
          Read
        </button>
        <select
          aria-label="New check-in period"
          value={picked}
          onChange={e => setChoice(Number(e.target.value))}
          disabled={off}
          style={SELECT}
        >
          {PING_CHOICES.map(m => <option key={m} value={m}>Every {formatPing(m)}</option>)}
        </select>
        <button type="button" className="btn btn-outline" disabled={off} style={button} onClick={() => void send({ kind: 'op_set', value: picked })}>
          Change
        </button>
        <span style={MUTED}>Silent after {silenceSpan(picked)} with no message.</span>
      </div>
      {query.error && <p role="alert" style={{ color: 'var(--error)', margin: '0.5rem 0 0 0' }}>{query.error.message}</p>}
      {status && <p role="status" style={{ margin: '0.5rem 0 0 0' }}>{status}</p>}
    </div>
  )
}
