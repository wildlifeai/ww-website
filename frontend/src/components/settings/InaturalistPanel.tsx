/**
 * InaturalistPanel: account-level iNaturalist controls (Settings).
 *
 * Home for "Sync now": pulls community IDs back into Wildlife Watcher for every linked
 * observation. This replaces the old per-grid "Sync iNaturalist IDs" bulk action; sync also runs
 * automatically on login (see InatAutoSync). IntegrationsSection decides whether it shows.
 */
import { useState } from 'react'
import { RefreshCw } from 'lucide-react'
import type { useINat } from '../../hooks/useINat'

export function InaturalistPanel({ inat }: { inat: ReturnType<typeof useINat> }) {
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)

  const syncAll = async () => {
    if (busy) return
    setBusy(true)
    setMsg(null)
    try {
      const r = await inat.sync()
      setMsg(
        `Synced ${r.updated} of ${r.checked}: ${r.research} research grade, ` +
        `${r.disagreement} disagreement, ${r.observations_written} community IDs written.`,
      )
    } catch (e) {
      setMsg((e as Error)?.message ?? 'Sync failed.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: '1rem 1.25rem', backgroundColor: 'var(--surface)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap', marginBottom: '0.5rem' }}>
        <h4 style={{ margin: 0, fontSize: '0.95rem' }}>iNaturalist</h4>
        <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
          {inat.connected ? `Connected${inat.username ? ` as @${inat.username}` : ''}, IDs sync when you sign in` : 'Not connected'}
        </span>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap' }}>
        {inat.connected ? (
          <>
            <button className="btn" onClick={syncAll} disabled={busy} style={{ fontSize: '0.82rem', display: 'inline-flex', alignItems: 'center', gap: '0.35rem' }}>
              <RefreshCw size={14} aria-hidden />
              {busy ? 'Syncing…' : 'Sync now'}
            </button>
            <button
              onClick={() => inat.disconnect()}
              style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '0.78rem', color: 'var(--text-muted)' }}
            >
              Disconnect
            </button>
          </>
        ) : (
          <button className="btn" onClick={() => inat.connect()} style={{ fontSize: '0.82rem' }}>
            Connect iNaturalist
          </button>
        )}
      </div>

      {msg && <p role="status" style={{ fontSize: '0.78rem', marginBottom: 0, marginTop: '0.75rem' }}>{msg}</p>}
    </div>
  )
}
