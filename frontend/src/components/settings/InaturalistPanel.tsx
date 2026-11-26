/**
 * InaturalistPanel: account-level iNaturalist controls (Settings).
 *
 * Home for "Sync now": pulls community IDs back into Wildlife Watcher for every linked
 * observation. This replaces the old per-grid "Sync iNaturalist IDs" bulk action; sync also runs
 * automatically on login (see InatAutoSync). IntegrationsSection decides whether it shows.
 * The sync is a job (lib/inatSync.ts): the panel follows it with useJob. Connecting is Toolkit's
 * paste box (a personal token), the one connect UI: no OAuth app is registered.
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { RefreshCw } from 'lucide-react'
import { useJob } from '../../hooks/useJob'
import type { useINat } from '../../hooks/useINat'
import { INAT_CONNECT_PATH } from '../../lib/inatPublish'
import { inatSyncRunning, inatSyncText } from '../../lib/inatSync'

export function InaturalistPanel({ inat }: { inat: ReturnType<typeof useINat> }) {
  const [starting, setStarting] = useState(false)
  const [syncJobId, setSyncJobId] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const { data: syncJob } = useJob(syncJobId)
  const busy = starting || (!!syncJobId && inatSyncRunning(syncJob))
  const status = msg ?? (syncJobId ? inatSyncText(syncJob) : null)

  const syncAll = async () => {
    if (busy) return
    setStarting(true)
    setMsg(null)
    setSyncJobId(null)
    try {
      setSyncJobId(await inat.sync())
    } catch (e) {
      setMsg((e as Error)?.message ?? 'Sync failed.')
    } finally {
      setStarting(false)
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
          <Link to={INAT_CONNECT_PATH} className="btn" style={{ fontSize: '0.82rem', textDecoration: 'none' }}>
            Connect it in Toolkit
          </Link>
        )}
      </div>

      {status && <p role="status" style={{ fontSize: '0.78rem', marginBottom: 0, marginTop: '0.75rem' }}>{status}</p>}
    </div>
  )
}
