/**
 * ApiKeysPanel: an organisation's public API keys (Settings), for its managers (#307).
 *
 * Create (the raw key is shown once), list and revoke. IntegrationsSection hides it for anyone
 * who manages no organisation, and while the public API is switched off. What the keys do and
 * the scopes are in documentation/resources/api-reference.md.
 */
import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useApiKeys, type ManagedOrg } from '../../hooks/useManagedOrgs'
import {
  API_KEY_SCOPES, createApiKey, expiryFromDate, isPublicApiDisabled, revokeApiKey,
  type ApiKeyInfo, type CreatedApiKey,
} from '../../lib/apiKeys'
import { ConfirmDialog } from '../common/ConfirmDialog'

const fmt = (s: string | null) => (s ? new Date(s).toLocaleDateString() : '—')
const errText = (e: unknown, fallback: string) => (e instanceof Error ? e.message : fallback)

const INPUT: React.CSSProperties = {
  padding: '0.4rem 0.55rem', fontSize: '0.82rem', border: '1px solid var(--border)',
  borderRadius: 'var(--radius)', background: 'var(--surface)', color: 'var(--text-color)',
}
const CELL: React.CSSProperties = { padding: '0.45rem 0.5rem', borderBottom: '1px solid var(--border)', textAlign: 'left' }

export function ApiKeysPanel({ orgs }: { orgs: ManagedOrg[] }) {
  const queryClient = useQueryClient()

  const [pickedOrg, setPickedOrg] = useState('')
  const orgId = pickedOrg || orgs[0]?.id || ''

  const keys = useApiKeys(orgId)

  const [name, setName] = useState('')
  const [scopes, setScopes] = useState<string[]>([])
  const [expiry, setExpiry] = useState('')
  const [created, setCreated] = useState<CreatedApiKey | null>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [revoking, setRevoking] = useState<ApiKeyInfo | null>(null)
  const [revokeError, setRevokeError] = useState<string | null>(null)
  const [revokeBusy, setRevokeBusy] = useState(false)

  if (orgs.length === 0 || isPublicApiDisabled(keys.error)) return null

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['apiKeys', orgId] })

  const create = async () => {
    setBusy(true)
    setMsg(null)
    try {
      setCreated(await createApiKey(orgId, name, scopes, expiryFromDate(expiry)))
      setName('')
      setScopes([])
      setExpiry('')
      refresh()
    } catch (e) {
      setMsg(errText(e, 'Could not create the key.'))
    } finally {
      setBusy(false)
    }
  }

  const revoke = async () => {
    if (!revoking) return
    const id = revoking.id
    setRevokeBusy(true)
    setRevokeError(null)
    try {
      await revokeApiKey(orgId, id)
      if (created?.id === id) setCreated(null)
      setRevoking(null)
      refresh()
    } catch (e) {
      setRevokeError(errText(e, 'Could not revoke the key.'))
    } finally {
      setRevokeBusy(false)
    }
  }

  const toggleScope = (s: string) =>
    setScopes(prev => (prev.includes(s) ? prev.filter(x => x !== s) : [...prev, s]))

  return (
    <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: '1rem 1.25rem', backgroundColor: 'var(--surface)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap', marginBottom: '0.75rem' }}>
        <h4 style={{ margin: 0, fontSize: '0.95rem' }}>API keys</h4>
        {orgs.length > 1 ? (
          <select
            value={orgId}
            onChange={e => { setPickedOrg(e.target.value); setCreated(null); setMsg(null) }}
            style={INPUT}
            aria-label="Organisation"
          >
            {orgs.map(o => <option key={o.id} value={o.id}>{o.name}</option>)}
          </select>
        ) : (
          <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>{orgs[0].name}</span>
        )}
      </div>

      {created && (
        <div style={{ border: '1px solid var(--primary)', borderRadius: 'var(--radius)', padding: '0.6rem 0.75rem', marginBottom: '0.75rem', fontSize: '0.82rem' }}>
          <div style={{ marginBottom: '0.4rem' }}>Copy this key now. It is not shown again.</div>
          <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
            <code style={{ wordBreak: 'break-all' }}>{created.key}</code>
            <button className="btn" style={{ fontSize: '0.78rem' }} onClick={() => navigator.clipboard?.writeText(created.key)}>Copy</button>
            <button className="btn" style={{ fontSize: '0.78rem' }} onClick={() => setCreated(null)}>Done</button>
          </div>
        </div>
      )}

      <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'center', marginBottom: '0.5rem' }}>
        <input value={name} onChange={e => setName(e.target.value)} placeholder="Key name" maxLength={100} style={{ ...INPUT, minWidth: 180 }} aria-label="Key name" />
        <label style={{ fontSize: '0.8rem', display: 'flex', gap: '0.35rem', alignItems: 'center' }}>
          Expires
          <input type="date" value={expiry} min={new Date().toLocaleDateString('en-CA')} onChange={e => setExpiry(e.target.value)} style={INPUT} />
        </label>
        <button className="btn" onClick={create} disabled={busy || !name.trim() || scopes.length === 0} style={{ fontSize: '0.82rem' }}>
          {busy ? 'Creating…' : 'Create key'}
        </button>
      </div>
      <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap', fontSize: '0.8rem', marginBottom: '0.75rem' }}>
        {API_KEY_SCOPES.map(s => (
          <label key={s} style={{ display: 'flex', gap: '0.25rem', alignItems: 'center' }}>
            <input type="checkbox" checked={scopes.includes(s)} onChange={() => toggleScope(s)} />
            <code>{s}</code>
          </label>
        ))}
      </div>

      {msg && <p role="alert" style={{ fontSize: '0.8rem', margin: '0 0 0.75rem 0', color: 'var(--error, #f44336)' }}>{msg}</p>}

      {keys.isLoading ? (
        <p style={{ color: 'var(--text-muted)', fontSize: '0.82rem', margin: 0 }}>Loading keys…</p>
      ) : keys.error ? (
        <p style={{ fontSize: '0.82rem', margin: 0, color: 'var(--error, #f44336)' }}>{errText(keys.error, 'Could not load the keys.')}</p>
      ) : !keys.data?.length ? (
        <p style={{ color: 'var(--text-muted)', fontSize: '0.82rem', margin: 0 }}>No keys.</p>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8rem' }}>
            <thead>
              <tr>
                {['Name', 'Key', 'Scopes', 'Created', 'Last used', 'Expires', ''].map(h => (
                  <th key={h} style={{ ...CELL, fontWeight: 600 }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {keys.data.map(k => (
                <tr key={k.id}>
                  <td style={CELL}>{k.name}</td>
                  <td style={CELL}><code>{k.key_prefix}…</code></td>
                  <td style={CELL}>{k.scopes.join(', ')}</td>
                  <td style={CELL}>{fmt(k.created_at)}</td>
                  <td style={CELL}>{fmt(k.last_used_at)}</td>
                  <td style={CELL}>{fmt(k.expires_at)}</td>
                  <td style={CELL}>
                    <button
                      onClick={() => { setRevokeError(null); setRevoking(k) }}
                      style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--error, #f44336)', fontSize: '0.8rem' }}
                    >
                      Revoke
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <ConfirmDialog
        open={!!revoking}
        title="Revoke API key"
        confirmLabel="Revoke"
        busyLabel="Revoking…"
        busy={revokeBusy}
        error={revokeError}
        onConfirm={revoke}
        onCancel={() => setRevoking(null)}
      >
        <p style={{ margin: 0 }}>Revoke <strong>{revoking?.name}</strong>? Anything using it stops working.</p>
      </ConfirmDialog>
    </div>
  )
}
