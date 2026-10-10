// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
/**
 * AdminDevicesPage — /admin/devices
 *
 * Read-only list of every device for a ww_admin, with its organisation and latest
 * deployment (#343). Device edits stay with organisation managers, through RLS.
 */
import { useMemo, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useIsAdmin } from '../hooks/useIsAdmin'
import { useAdminDevices, type AdminDevice } from '../hooks/useAdminDevices'
import { DataTable, type Column } from '../components/ui/DataTable'
import { FilterSelect } from '../components/ui/ControlBar'

const columns: Column<AdminDevice>[] = [
  { key: 'name', label: 'Name', cellStyle: { fontWeight: 500 } },
  { key: 'bluetooth_id', label: 'Bluetooth id' },
  { key: 'device_eui', label: 'EUI', render: d => d.device_eui ?? '—' },
  { key: 'organisation', label: 'Organisation', render: d => d.organisation?.name ?? '—', getValue: d => d.organisation?.name },
  {
    key: 'latest_deployment', label: 'Latest deployment',
    render: d => d.latest_deployment ? (
      <div>
        <div>{d.latest_deployment.name}{d.latest_deployment.deployment_end && <span style={{ opacity: 0.6 }}> (ended)</span>}</div>
        <div style={{ fontSize: '0.72rem', opacity: 0.6 }}>{d.latest_deployment.project.name}</div>
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

  if (isAdmin === false) return <Navigate to="/" replace />

  return (
    <div style={{ maxWidth: 1160 }}>
      <h2 style={{ margin: '0 0 1rem 0' }}>📡 Devices</h2>
      {error && <p style={{ color: 'var(--error)', marginBottom: '1rem' }}>⚠ {error.message}</p>}
      {isAdmin === null || isLoading ? (
        <p style={{ opacity: 0.5 }}>Loading devices…</p>
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
