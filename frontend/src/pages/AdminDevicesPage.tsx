// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
/**
 * AdminDevicesPage: /admin/devices
 *
 * Every device for a ww_admin, with its latest deployment and that project's organisation
 * (#343): cameras belong to no organisation (ww-backend#346). The ww_admin sets or corrects a
 * camera's LoRaWAN DevEUI in its row (#525, lib/deviceEui.ts), written as the signed-in user.
 */
import { useMemo, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { Pencil } from 'lucide-react'
import { supabase } from '../config/supabase'
import { useIsAdmin } from '../hooks/useIsAdmin'
import { ADMIN_DEVICES_KEY, useAdminDevices, type AdminDevice } from '../hooks/useAdminDevices'
import { DataTable, type Column } from '../components/ui/DataTable'
import { FilterSelect } from '../components/ui/ControlBar'
import { saveDeviceEui } from '../lib/deviceEui'

const MUTED: React.CSSProperties = { color: 'var(--text-muted)' }
const INPUT: React.CSSProperties = {
  padding: '0.25rem 0.4rem', fontSize: '0.75rem', fontFamily: 'monospace', width: '13rem', border: '1px solid var(--border)',
  borderRadius: 'var(--radius)', background: 'var(--surface)', color: 'var(--text-color)',
}
const SMALL_BUTTON: React.CSSProperties = { padding: '0.25rem 0.6rem', fontSize: '0.75rem' }

/** The camera's DevEUI, with an edit in place: the messages of a camera with none never link. */
function EuiCell({ device }: { device: AdminDevice }) {
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (draft === null) {
    return (
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.35rem' }}>
        {device.device_eui ?? <span style={MUTED}>-</span>}
        <button
          type="button"
          aria-label={`Edit the DevEUI of ${device.name}`}
          title="Edit DevEUI"
          onClick={() => { setError(null); setDraft(device.device_eui ?? '') }}
          style={{ background: 'transparent', border: 'none', padding: '0.125rem', cursor: 'pointer', lineHeight: 0, color: 'var(--text-muted)' }}
        >
          <Pencil size={13} aria-hidden />
        </button>
      </span>
    )
  }

  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      await saveDeviceEui(supabase, device.id, draft)
      await queryClient.invalidateQueries({ queryKey: ADMIN_DEVICES_KEY })
      setDraft(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <form onSubmit={e => { e.preventDefault(); void save() }} style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
      <span style={{ display: 'flex', gap: '0.35rem', alignItems: 'center' }}>
        <input
          aria-label={`DevEUI of ${device.name}`}
          value={draft}
          onChange={e => setDraft(e.target.value)}
          placeholder="70B3D57ED0012345"
          spellCheck={false}
          style={INPUT}
        />
        <button type="submit" className="btn" disabled={saving} style={SMALL_BUTTON}>{saving ? 'Saving…' : 'Save'}</button>
        <button type="button" className="btn btn-outline" disabled={saving} onClick={() => setDraft(null)} style={SMALL_BUTTON}>Cancel</button>
      </span>
      {error && <span role="alert" style={{ color: 'var(--error)', fontSize: '0.75rem' }}>{error}</span>}
    </form>
  )
}

const columns: Column<AdminDevice>[] = [
  { key: 'name', label: 'Name', cellStyle: { fontWeight: 500 } },
  { key: 'bluetooth_id', label: 'Bluetooth id' },
  { key: 'device_eui', label: 'EUI', render: d => <EuiCell device={d} /> },
  { key: 'organisation', label: 'Organisation', render: d => d.organisation?.name ?? '—', getValue: d => d.organisation?.name },
  {
    key: 'latest_deployment', label: 'Latest deployment',
    render: d => d.latest_deployment ? (
      <div>
        <div>{d.latest_deployment.name}{d.latest_deployment.deployment_end && <span style={{ color: 'var(--text-muted)' }}> (ended)</span>}</div>
        <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>{d.latest_deployment.project.name}</div>
      </div>
    ) : '—',
    getValue: d => d.latest_deployment ? `${d.latest_deployment.name} ${d.latest_deployment.project.name}` : '',
  },
  {
    key: 'deployment_start', label: 'Since',
    render: d => d.latest_deployment ? new Date(d.latest_deployment.deployment_start).toLocaleDateString() : '—',
    getValue: d => d.latest_deployment?.deployment_start,
  },
]

export function AdminDevicesPage() {
  const isAdmin = useIsAdmin()
  const { data: devices = [], isLoading, error } = useAdminDevices(isAdmin === true)
  const [orgId, setOrgId] = useState('')

  const orgOptions = useMemo(() => {
    const byId = new Map(devices.flatMap(d => d.organisation ? [[d.organisation.id, d.organisation.name] as const] : []))
    return [...byId].map(([value, label]) => ({ value, label })).sort((a, b) => a.label.localeCompare(b.label))
  }, [devices])
  const rows = orgId ? devices.filter(d => d.organisation?.id === orgId) : devices

  // Nothing until the role check answers, so a non-admin never sees the page; then redirect.
  if (isAdmin === null) return null
  if (isAdmin === false) return <Navigate to="/" replace />

  return (
    <div style={{ maxWidth: 1160 }}>
      <h2 style={{ margin: '0 0 1rem 0' }}>Devices</h2>
      {error && <p role="alert" style={{ color: 'var(--error)', marginBottom: '1rem' }}>{error.message}</p>}
      {isLoading ? (
        <p style={{ color: 'var(--text-muted)' }}>Loading devices…</p>
      ) : (
        <DataTable<AdminDevice>
          columns={columns} rows={rows} rowKey={d => d.id}
          searchable searchPlaceholder="Search name, Bluetooth id or EUI…" exportFilename="devices"
          emptyMessage="No devices." pageSize={50}
          toolbar={<FilterSelect value={orgId} onChange={setOrgId} options={orgOptions} placeholder="All organisations" />}
        />
      )}
    </div>
  )
}
