// Copyright (c) 2024
// SPDX-License-Identifier: GPL-3.0-or-later
//
// NotificationsPage, /notifications (opened from the avatar menu).
// Lists the signed-in user's notifications with type/unread filters and mark-read
// actions. The filters run in the query and the list loads a page at a time
// (UI audit 01 E1). Degrades to a short note when the notifications table isn't
// deployed in this environment yet (see useNotifications).
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Bell, CircleCheck, Info, PawPrint, WifiOff, type LucideIcon } from 'lucide-react'
import { NOTIFICATIONS_PAGE, useNotifications, type AppNotification } from '../hooks/useNotifications'

/** The table's four types (ww-backend `54_notifications.sql`), in the filter's order. */
const TYPE_META: Record<string, { icon: LucideIcon; label: string }> = {
  species_detection: { icon: PawPrint,    label: 'Species detection' },
  camera_silent:     { icon: WifiOff,     label: 'Camera silent' },
  upload_complete:   { icon: CircleCheck, label: 'Upload complete' },
  system:            { icon: Info,        label: 'System' },
}

const SELECT_STYLE: React.CSSProperties = {
  padding: '0.35rem 0.5rem', border: '1px solid var(--border)', borderRadius: 'var(--radius)',
  background: 'var(--surface)', color: 'var(--text-color)', fontSize: '0.8rem',
}

function timeAgo(iso: string): string {
  const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 60) return 'just now'
  const m = Math.floor(s / 60); if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60); if (h < 24) return `${h}h ago`
  const d = Math.floor(h / 24); if (d < 7) return `${d}d ago`
  return new Date(iso).toLocaleDateString()
}

export function NotificationsPage() {
  const navigate = useNavigate()
  const [typeFilter, setTypeFilter] = useState<string>('')
  const [unreadOnly, setUnreadOnly] = useState(false)
  const [limit, setLimit] = useState(NOTIFICATIONS_PAGE)
  const { items, available, loading, fetching, hasMore, unreadCount, markRead, markAllRead } =
    useNotifications({ type: typeFilter, unreadOnly }, limit)

  // A new filter starts again from the first page.
  const changeType = (t: string) => { setTypeFilter(t); setLimit(NOTIFICATIONS_PAGE) }
  const changeUnread = (v: boolean) => { setUnreadOnly(v); setLimit(NOTIFICATIONS_PAGE) }

  const onOpen = (n: AppNotification) => {
    if (!n.read_at) markRead(n.id)
    const link = (n.data as { link?: string } | null)?.link
    if (typeof link === 'string' && link.startsWith('/')) navigate(link)
  }

  return (
    <div style={{ maxWidth: 760 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: '1rem' }}>
        <h2 style={{ margin: '0 0 0.375rem 0' }}>Notifications</h2>
        {available && unreadCount > 0 && (
          <button className="btn" style={{ fontSize: '0.78rem', padding: '0.3rem 0.7rem' }} onClick={() => markAllRead()}>
            Mark all read ({unreadCount})
          </button>
        )}
      </div>

      {loading ? (
        <p style={{ color: 'var(--text-muted)' }}>Loading…</p>
      ) : !available ? (
        <p style={{ color: 'var(--text-muted)', fontSize: '0.875rem' }}>
          Notifications aren&apos;t turned on in this environment yet.
        </p>
      ) : (
        <>
          {/* Filters, applied in the query */}
          <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap', margin: '0.75rem 0 1rem' }}>
            <select
              value={typeFilter}
              onChange={e => changeType(e.target.value)}
              aria-label="Notification type"
              style={SELECT_STYLE}
            >
              <option value="">All types</option>
              {Object.entries(TYPE_META).map(([t, meta]) => <option key={t} value={t}>{meta.label}</option>)}
            </select>
            <label style={{ display: 'flex', alignItems: 'center', gap: '0.35rem', fontSize: '0.8rem' }}>
              <input type="checkbox" checked={unreadOnly} onChange={e => changeUnread(e.target.checked)} />
              Unread only
            </label>
          </div>

          {items.length === 0 ? (
            <div className="card" style={{ padding: '2rem', textAlign: 'center' }}>
              <div style={{ fontWeight: 600 }}>You&apos;re all caught up</div>
              <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)', marginTop: '0.3rem' }}>No notifications match this filter.</div>
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
              {items.map(n => {
                const meta = TYPE_META[n.type] ?? { icon: Bell, label: n.type }
                const Icon = meta.icon
                return (
                  <div
                    key={n.id}
                    onClick={() => onOpen(n)}
                    style={{
                      display: 'flex', gap: '0.75rem', alignItems: 'flex-start', cursor: 'pointer',
                      padding: '0.75rem 0.9rem', borderRadius: 'var(--radius)',
                      border: '1px solid var(--border)',
                      background: n.read_at ? 'transparent' : 'rgba(76,175,80,0.06)',
                    }}
                  >
                    <Icon size={18} aria-label={meta.label} style={{ flexShrink: 0, marginTop: '0.1rem', color: 'var(--text-muted)' }} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'baseline', justifyContent: 'space-between' }}>
                        <span style={{ fontWeight: n.read_at ? 500 : 700, fontSize: '0.875rem' }}>{n.title}</span>
                        <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>{timeAgo(n.created_at)}</span>
                      </div>
                      {n.body && <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '0.15rem' }}>{n.body}</div>}
                    </div>
                    {!n.read_at && (
                      <button
                        onClick={e => { e.stopPropagation(); markRead(n.id) }}
                        title="Mark read"
                        style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--primary)', fontSize: '0.72rem', whiteSpace: 'nowrap' }}
                      >
                        Mark read
                      </button>
                    )}
                  </div>
                )
              })}
              {hasMore && (
                <button
                  type="button"
                  disabled={fetching}
                  onClick={() => setLimit(l => l + NOTIFICATIONS_PAGE)}
                  style={{ ...SELECT_STYLE, alignSelf: 'center', cursor: 'pointer', marginTop: '0.5rem' }}
                >
                  {fetching ? 'Loading…' : 'Load more'}
                </button>
              )}
            </div>
          )}
        </>
      )}
    </div>
  )
}
