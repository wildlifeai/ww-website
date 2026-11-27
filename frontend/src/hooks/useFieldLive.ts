// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// useFieldLive: the Realtime page's live updates, one Supabase Realtime channel while it shows
// deployments. Returns whether the channel is subscribed, for the page's "Live" dot.
//
// - lorawan_messages INSERT: the camera's last message time, with no read; an empty frame counts.
// - lorawan_parsed_messages INSERT: lorawan-ingest stores the decoded values a moment after the
//   message, so a re-read on the message alone could miss them. The decoded row reads its message
//   (one request) for the Messages tab and the camera's values (two) for the Cameras table.
// - lorawan_downlinks, any change: a settings request moved on (queued, sent, answered, failed,
//   expired, #521). The picked camera's panel (components/data/CheckInPanel.tsx) reads its request
//   and reply again; so does a settings reply, which can come from a request sent elsewhere.
// - Realtime does not resend changes missed while disconnected, and focus refetch is off site-wide
//   (lib/queryClient.ts), so every re-subscribe, and a return to the tab (at most every 30 s),
//   reads the page again.
//
// The three tables are in the supabase_realtime publication (ww-backend migration 20260711043000,
// lorawan_downlinks with #405) and
// Realtime checks each change against the subscriber's read policy, so a change from another
// project never arrives. The decoded row has no deployment_id to filter on: one from a camera
// the page does not show is dropped by its device_id before any read.
import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { supabase } from '../config/supabase'
import {
  fetchDeploymentUpdate, realtimeKeys, withEntry, withLastSeen, type FieldDeployment, type Telemetry,
} from '../lib/realtimeReads'
import { fetchFeedMessage, isFeedKind, isSettingsReply, withLiveRow, type FeedState } from '../lib/lorawanFeed'
import { shouldRefreshOnReturn, subscribeToTabReturn } from '../lib/tabReturnRefresh'

/** The channel name's start; e2e/specs/04-lorawan-realtime.spec.ts waits for it to subscribe. */
export const FIELD_CHANNEL = 'field-lorawan-live'
// Each subscription gets its own name: supabase.channel() hands back a channel of the same name
// that is still leaving, and subscribing that one does nothing (gotchas.md).
let channelCount = 0

export function useFieldLive(deployments: FieldDeployment[]): boolean {
  const queryClient = useQueryClient()
  const [live, setLive] = useState(false)

  useEffect(() => {
    if (deployments.length === 0) return
    const ids = deployments.map(d => d.id)
    const shown = new Set(ids)
    const devices = new Set(deployments.map(d => d.device_id))
    const telemetryKey = realtimeKeys.telemetry(ids)
    const detectionsKey = realtimeKeys.detections(ids)

    const readAgain = () => { void queryClient.invalidateQueries({ queryKey: realtimeKeys.all }) }
    // Only the open panel's read is active, so this reads nothing for any other camera.
    const readCheckIn = (id: string) => { void queryClient.invalidateQueries({ queryKey: realtimeKeys.checkIn(id) }) }

    const refreshCamera = async (id: string) => {
      const { telemetry, detections } = await fetchDeploymentUpdate(supabase, id)
      queryClient.setQueryData<Record<string, Telemetry>>(telemetryKey, old => old && withEntry(old, id, telemetry))
      queryClient.setQueryData<Record<string, number>>(detectionsKey, old => old && withEntry(old, id, detections))
    }

    const onDecoded = async (row: { lorawan_message_id: string; device_id: string | null; kind: string }) => {
      // A settings reply re-reads the camera too: one sent after an error carries its health.
      if (!isFeedKind(row.kind) || (row.device_id && !devices.has(row.device_id))) return
      try {
        const message = await fetchFeedMessage(supabase, row.lorawan_message_id)
        if (!message || !shown.has(message.deploymentId)) return
        queryClient.setQueryData<FeedState>(realtimeKeys.feed(ids), s => s && withLiveRow(s, message))
        if (isSettingsReply(message)) readCheckIn(message.deploymentId)
        await refreshCamera(message.deploymentId)
      } catch {
        // Read everything again, which shows the error if the database still refuses.
        readAgain()
      }
    }

    let joined = false
    let lastRead = Date.now()
    const catchUp = () => { lastRead = Date.now(); readAgain() }

    const channel = supabase
      .channel(`${FIELD_CHANNEL}-${++channelCount}`)
      .on('postgres_changes',
        { event: 'INSERT', schema: 'public', table: 'lorawan_messages' },
        (payload) => {
          const row = payload.new as { deployment_id: string | null; received_at: string | null }
          const id = row.deployment_id
          if (!id || !row.received_at || !shown.has(id)) return
          const at = row.received_at
          queryClient.setQueryData<Record<string, Telemetry>>(telemetryKey, old => old && withLastSeen(old, id, at))
        })
      .on('postgres_changes',
        { event: 'INSERT', schema: 'public', table: 'lorawan_parsed_messages' },
        (payload) => { void onDecoded(payload.new as Parameters<typeof onDecoded>[0]) })
      .on('postgres_changes',
        { event: '*', schema: 'public', table: 'lorawan_downlinks' },
        (payload) => {
          const id = (payload.new as { deployment_id?: string } | null)?.deployment_id
          if (id && shown.has(id)) readCheckIn(id)
        })
      .subscribe((status) => {
        const up = status === 'SUBSCRIBED'
        setLive(up)
        // The first subscribe follows the page's own reads; a later one is a reconnect.
        if (up && joined) catchUp()
        if (up) joined = true
      })
    const stopTabReturn = subscribeToTabReturn(() => {
      if (shouldRefreshOnReturn(lastRead, Date.now())) catchUp()
    })
    return () => {
      stopTabReturn()
      void supabase.removeChannel(channel)
    }
  }, [deployments, queryClient])

  return live
}
