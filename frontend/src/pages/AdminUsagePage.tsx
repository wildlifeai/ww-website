// Copyright (c) 2024
// SPDX-License-Identifier: GPL-3.0-or-later
/**
 * AdminUsagePage — /admin/usage
 *
 * System-admin view of per-user usage across photos, rendition storage, and AI
 * compute, backed by the `admin_user_usage` RPC (SECURITY DEFINER, ww_admin-
 * gated). Admins can set soft limits per user (advisory — never blocks usage).
 */
/* eslint-disable react-hooks/set-state-in-effect */
import { useEffect, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { TriangleAlert } from 'lucide-react'
import { supabase } from '../config/supabase'
import { useAuth } from '../hooks/useAuth'
import { useIsAdmin } from '../hooks/useIsAdmin'
import { DataTable, type Column } from '../components/ui/DataTable'
import { Modal } from '../components/ui/Modal'

interface UsageRow {
  user_id: string
  email: string | null
  full_name: string | null
  photos_uploaded: number
  storage_bytes: number
  compute_runs: number
  compute_seconds: number
  last_upload: string | null
  last_active: string | null
  max_photos: number | null
  max_storage_bytes: number | null
  max_compute_seconds: number | null
  over_quota: boolean
}

function fmtBytes(n: number | null): string {
  if (n == null) return '—'
  if (n === 0) return '0 B'
  const u = ['B', 'KB', 'MB', 'GB', 'TB']; let v = n, i = 0
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(v < 10 && i > 0 ? 1 : 0)} ${u[i]}`
}
function fmtDuration(sec: number | null): string {
  if (sec == null) return '—'
  if (sec === 0) return '0m'
  const h = Math.floor(sec / 3600), m = Math.round((sec % 3600) / 60)
  return h > 0 ? `${h}h ${m}m` : `${m}m`
}
function fmtDate(s: string | null): string { return s ? new Date(s).toLocaleDateString() : '—' }
/** A user's soft limits in words, or "None" when every one is unlimited. */
function limitsText(r: UsageRow): string {
  const parts = [
    r.max_photos != null && `${r.max_photos.toLocaleString()} photos`,
    r.max_storage_bytes != null && fmtBytes(r.max_storage_bytes),
    r.max_compute_seconds != null && `${fmtDuration(r.max_compute_seconds)} AI`,
  ].filter(Boolean)
  return parts.length ? parts.join(' · ') : 'None'
}

const LINK_BUTTON: React.CSSProperties = {
  border: 'none', background: 'none', padding: 0, cursor: 'pointer',
  color: 'var(--primary)', textDecoration: 'underline', fontSize: 'inherit',
}

const INPUT: React.CSSProperties = {
  width: 110, padding: '0.35rem 0.5rem', fontSize: '0.85rem', border: '1px solid var(--border)',
  borderRadius: 'var(--radius)', background: 'var(--surface)', color: 'var(--text-color)',
}

// Modal: edit a user's three soft limits. Admin enters photos / storage MB /
// compute minutes; stored as photos / bytes / seconds. Blank = unlimited.
function QuotaEditModal({ row, onClose, onSaved }: { row: UsageRow; onClose: () => void; onSaved: () => void }) {
  const [photos, setPhotos] = useState(row.max_photos == null ? '' : String(row.max_photos))
  const [mb, setMb] = useState(row.max_storage_bytes == null ? '' : String(Math.round(row.max_storage_bytes / 1_048_576)))
  const [mins, setMins] = useState(row.max_compute_seconds == null ? '' : String(Math.round(row.max_compute_seconds / 60)))
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const num = (s: string): number | null => {
    const t = s.trim(); if (t === '') return null
    const n = Math.floor(Number(t)); return Number.isNaN(n) ? null : Math.max(0, n)
  }

  const save = async () => {
    setBusy(true); setErr(null)
    const patch = {
      user_id: row.user_id,
      max_photos: num(photos),
      max_storage_bytes: mb.trim() === '' ? null : (num(mb) ?? 0) * 1_048_576,
      max_compute_seconds: mins.trim() === '' ? null : (num(mins) ?? 0) * 60,
      updated_at: new Date().toISOString(),
    }
    const { error } = await supabase.from('upload_quotas').upsert(patch)
    setBusy(false)
    if (error) setErr(error.message)
    else { onSaved(); onClose() }
  }

  const field = (label: string, hint: string, value: string, set: (s: string) => void) => (
    <label style={{ display: 'flex', flexDirection: 'column', gap: '0.2rem', fontSize: '0.8rem' }}>
      <span>{label} <span style={{ opacity: 0.5 }}>{hint}</span></span>
      <input type="number" min={0} value={value} onChange={e => set(e.target.value)} placeholder="∞ (no limit)" style={INPUT} />
    </label>
  )

  return (
    <Modal open onClose={onClose} title={`Limits for ${row.full_name || row.email || row.user_id.slice(0, 8)}`}>
      <p style={{ fontSize: '0.82rem', color: 'var(--text-muted)', marginTop: 0 }}>Soft limits warn, they never block. Leave blank for no limit.</p>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
        {field('Photos', '(count)', photos, setPhotos)}
        {field('Storage', '(MB)', mb, setMb)}
        {field('AI compute', '(minutes)', mins, setMins)}
      </div>
      {err && <p role="alert" style={{ color: 'var(--error)', fontSize: '0.8rem' }}>{err}</p>}
      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '0.6rem', marginTop: '1rem' }}>
        <button onClick={onClose} disabled={busy} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-muted)' }}>Cancel</button>
        <button className="btn" onClick={save} disabled={busy}>{busy ? 'Saving…' : 'Save limits'}</button>
      </div>
    </Modal>
  )
}

export function AdminUsagePage() {
  const { user, loading: authLoading } = useAuth()
  const isAdmin = useIsAdmin()
  const [rows, setRows] = useState<UsageRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [editRow, setEditRow] = useState<UsageRow | null>(null)
  const [reloadKey, setReloadKey] = useState(0)

  // Only fetch once we know the caller is an admin — avoids an unauthorized call.
  useEffect(() => {
    if (!user || isAdmin !== true) return
    let cancelled = false
    setLoading(true)
    supabase.rpc('admin_user_usage').then(({ data, error: err }) => {
      if (cancelled) return
      if (err) setError(err.message)
      else setRows(((data ?? []) as any[]).map(r => ({
        ...r,
        photos_uploaded: Number(r.photos_uploaded),
        storage_bytes: Number(r.storage_bytes),
        compute_runs: Number(r.compute_runs),
        compute_seconds: Number(r.compute_seconds),
        max_photos: r.max_photos == null ? null : Number(r.max_photos),
        max_storage_bytes: r.max_storage_bytes == null ? null : Number(r.max_storage_bytes),
        max_compute_seconds: r.max_compute_seconds == null ? null : Number(r.max_compute_seconds),
      })))
      setLoading(false)
    })
    return () => { cancelled = true }
  }, [user, isAdmin, reloadKey])

  // Nothing until the role check answers, so a non-admin never sees the page; then redirect.
  if (authLoading || isAdmin === null) return null
  if (isAdmin === false) return <Navigate to="/" replace />

  const totals = rows.reduce(
    (a, r) => ({ photos: a.photos + r.photos_uploaded, bytes: a.bytes + r.storage_bytes, sec: a.sec + r.compute_seconds }),
    { photos: 0, bytes: 0, sec: 0 },
  )
  const overCount = rows.filter(r => r.over_quota).length

  const columns: Column<UsageRow>[] = [
    {
      key: 'full_name', label: 'User', cellStyle: { fontWeight: 500 },
      render: r => (
        <div>
          <div>{r.full_name || '—'}</div>
          <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>{r.email || r.user_id.slice(0, 8)}</div>
        </div>
      ),
      getValue: r => r.full_name ?? r.email ?? '',
    },
    { key: 'photos_uploaded', label: 'Photos', cellStyle: { textAlign: 'right' },
      render: r => (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.25rem', color: r.over_quota ? '#ef4444' : undefined, fontWeight: r.over_quota ? 700 : undefined }}>
          {r.photos_uploaded.toLocaleString()}
          {r.over_quota && <TriangleAlert size={12} aria-label="Over a limit" />}
        </span>
      ),
      getValue: r => r.photos_uploaded },
    { key: 'storage_bytes', label: 'Storage', cellStyle: { textAlign: 'right' }, render: r => fmtBytes(r.storage_bytes), getValue: r => r.storage_bytes },
    { key: 'compute_seconds', label: 'AI compute', cellStyle: { textAlign: 'right' },
      render: r => r.compute_runs ? `${fmtDuration(r.compute_seconds)} · ${r.compute_runs} run${r.compute_runs !== 1 ? 's' : ''}` : '—',
      getValue: r => r.compute_seconds },
    { key: '_limits', label: 'Limits', sortable: false,
      render: r => (
        <span style={{ display: 'inline-flex', gap: '0.5rem', alignItems: 'baseline', fontSize: '0.78rem' }}>
          <span>{limitsText(r)}</span>
          <button onClick={() => setEditRow(r)} aria-label={`Edit limits for ${r.full_name || r.email || 'this user'}`} style={LINK_BUTTON}>Edit</button>
        </span>
      ),
      getValue: r => limitsText(r) },
    { key: 'last_upload', label: 'Last upload', render: r => fmtDate(r.last_upload), getValue: r => r.last_upload ?? '' },
    { key: 'last_active', label: 'Last active', render: r => fmtDate(r.last_active), getValue: r => r.last_active ?? '' },
  ]

  return (
    <div style={{ maxWidth: 1160 }}>
      <h2 style={{ margin: '0 0 0.375rem 0' }}>User usage</h2>
      <p style={{ color: 'var(--text-muted)', fontSize: '0.9rem', margin: '0 0 1rem 0' }}>
        Photos, rendition storage and AI compute per user. Limits warn, they never block.
      </p>

      {error && <p role="alert" style={{ color: 'var(--error)', marginBottom: '1rem' }}>{error}</p>}

      {!loading && rows.length > 0 && (
        <div style={{ display: 'flex', gap: '0.75rem', marginBottom: '1rem', flexWrap: 'wrap' }}>
          {([['users', rows.length], ['photos', totals.photos.toLocaleString()], ['storage', fmtBytes(totals.bytes)], ['AI compute', fmtDuration(totals.sec)], ['over limit', overCount]] as [string, string | number][]).map(([label, value]) => (
            <div key={label} className="glass-card" style={{ padding: '0.75rem 1rem', minWidth: 110 }}>
              <div style={{ fontSize: '1.4rem', fontWeight: 700, color: label === 'over limit' && overCount ? '#ef4444' : undefined }}>{value}</div>
              <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>{label}</div>
            </div>
          ))}
        </div>
      )}

      {loading ? (
        <p style={{ color: 'var(--text-muted)' }}>Loading usage…</p>
      ) : (
        <DataTable<UsageRow>
          columns={columns} rows={rows} rowKey={r => r.user_id}
          searchable searchPlaceholder="Search users…" exportFilename="user-usage"
          emptyMessage="No usage data." pageSize={50}
        />
      )}

      {editRow && <QuotaEditModal row={editRow} onClose={() => setEditRow(null)} onSaved={() => setReloadKey(k => k + 1)} />}
    </div>
  )
}
