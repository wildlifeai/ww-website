// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// LorawanMessages: the Messages tab of the Realtime page, the shown deployments' LoRaWAN messages
// newest first (lib/lorawanFeed.ts). Rows that arrived while the page is open keep the table's
// selection tint.
import { useMemo } from 'react'
import { DataTable, type Column } from '../ui/DataTable'
import {
  agoLabel, batteryText, feedTime, isNew, messageText, sdFreeText, type FeedRow,
} from '../../lib/lorawanFeed'
import type { useMessageFeed } from '../../hooks/useMessageFeed'

const MUTED: React.CSSProperties = { color: 'var(--text-muted)' }

export function LorawanMessages({ feed, names }: {
  feed: ReturnType<typeof useMessageFeed>
  /** Camera name by deployment id. */
  names: Map<string, string>
}) {
  const { state } = feed
  const rows = state?.rows
  const arrived = useMemo(() => {
    const ids = new Set<string>()
    if (state) for (const r of state.rows) if (isNew(state, r)) ids.add(r.id)
    return ids
  }, [state])

  const columns = useMemo<Column<FeedRow>[]>(() => [
    { key: 'receivedAt', label: 'Time', cellStyle: { whiteSpace: 'nowrap' },
      render: r => <span title={agoLabel(r.receivedAt)}>{feedTime(r.receivedAt)}</span> },
    { key: 'camera', label: 'Camera', cellStyle: { fontWeight: 500 },
      getValue: r => names.get(r.deploymentId) ?? '' },
    { key: 'message', label: 'Message', cellStyle: { whiteSpace: 'nowrap' },
      render: r => <span style={{ fontWeight: r.detections ? 600 : undefined }}>{messageText(r)}</span>,
      getValue: messageText },
    { key: 'battery', label: 'Battery', cellStyle: { whiteSpace: 'nowrap' }, getValue: batteryText },
    { key: 'sdFree', label: 'SD free', getValue: sdFreeText },
  ], [names])

  if (feed.loading) return <p style={MUTED}>Loading messages…</p>

  return (
    <>
      <DataTable<FeedRow>
        columns={columns}
        rows={rows ?? []}
        rowKey={r => r.id}
        selectedKeys={arrived}
        searchable
        searchPlaceholder="Search messages…"
        exportFilename="realtime-messages"
        emptyMessage="No messages yet."
      />
      {feed.canLoadOlder && (
        <div style={{ textAlign: 'center', marginTop: '0.75rem' }}>
          <button type="button" className="btn btn-outline" onClick={() => void feed.loadOlder()} disabled={feed.loadingOlder}>
            {feed.loadingOlder ? 'Loading…' : 'Load older'}
          </button>
        </div>
      )}
      {feed.olderError && <p role="alert" style={{ color: 'var(--error)', fontSize: '0.8125rem' }}>{feed.olderError}</p>}
    </>
  )
}
