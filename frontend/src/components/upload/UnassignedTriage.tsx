/**
 * UnassignedTriage, resolve photos that arrive without a deployment.
 *
 * Why this exists: media rows are only created for files that reach Google
 * Drive, and `candidates` in the drive-upload job requires a deployment_id
 * (backend/app/jobs/definitions.py). A photo with no deployment is therefore
 * never stored and never appears anywhere, while the dock still reports the
 * run as complete. Silently dropping a card's photos is the failure this
 * screen prevents.
 *
 * Photos are grouped into capture sessions (same card folder, gaps under
 * SESSION_GAP_MS) so the operator decides per shoot rather than per photo,
 * and each session can be bound to an existing deployment or turned into a
 * new one pre-filled from the photos themselves. With many sessions, one
 * action assigns every session left, or creates a deployment for each
 * (UI audit 05 #6). Decided sessions collapse to a line.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { PageHelp } from '../common/PageHelp'
import { apiClient } from '../../lib/apiClient'
import { deploymentPickerGroups, pickerLabels, type UploadTarget } from '../../lib/uploadTargets'
import { DeploymentPicker } from './DeploymentPicker'
import { buildSessions, defaultDeploymentName, fmt, planBulkCreate, span } from './unassignedSessions'
import type { TriageSession } from './unassignedSessions'

interface Props {
  files: File[]
  filePaths: string[]
  /** Deployment UUID each file carries in EXIF (0xF200), aligned to `files`; null where absent. */
  exifIds?: (string | null)[]
  /** Indices of files that resolve to no deployment from their EXIF id or the card's folder structure. */
  unresolved: number[]
  /**
   * Photos that already resolved to a deployment, grouped by it. They are not
   * triaged, but the screen names them so the user knows the rest of the card
   * is accounted for and the footer's "ready to upload" count is honest.
   */
  matched?: { label: string; count: number }[]
  /** Photos whose deployment is in a project the user cannot upload to; they are left out. */
  blockedCount?: number
  /** Deployments the user can upload to. */
  deployments: UploadTarget[]
  /** Projects the user can upload to, offered when a session becomes a new deployment. */
  projects: { id: string; name: string }[]
  onCancel: () => void
  /** A deployment was created here, so the page's list is out of date. */
  onDeploymentCreated?: () => void
  /** Sessions the user resolved; skipped ones are excluded. */
  onDone: (assignments: { deploymentId: string; indices: number[] }[], skipped: number) => void
}

/** What POST /api/deployments returns. */
interface CreatedDeployment {
  id: string
  project_id: string
  name: string
  location_name: string | null
  deployment_start: string | null
}

async function createDeployment(projectId: string, name: string, exifDeploymentId: string | null): Promise<UploadTarget> {
  // When the camera stamped a deployment id, create the row under that id: the
  // phone that configured the device holds the same id locally, so its later
  // sync converges on this row instead of producing a second deployment.
  const dep = (await apiClient.post('/api/deployments', {
    project_id: projectId,
    name,
    ...(exifDeploymentId ? { id: exifDeploymentId } : {}),
  })) as CreatedDeployment
  return {
    id: dep.id,
    project_id: dep.project_id ?? projectId,
    name: dep.name ?? name,
    location_name: dep.location_name ?? null,
    deployment_start: dep.deployment_start ?? null,
  }
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`

/** A few representative frames from the session (first, last, and between). */
function useSamplePicks(indices: number[]): number[] {
  return useMemo(() => {
    if (indices.length <= 4) return indices
    const step = (indices.length - 1) / 3
    return [0, 1, 2, 3].map((n) => indices[Math.round(n * step)])
  }, [indices])
}

/**
 * One thumbnail that owns its object URL. The URL is created inside the
 * effect and written straight to the element, and revoked in the cleanup, so
 * the pair always runs together. An earlier version created the URL in a
 * useState initialiser and only revoked it in the cleanup; React StrictMode
 * runs the cleanup and re-runs the effect on mount without re-running the
 * initialiser, so in development every thumbnail pointed at a revoked URL and
 * rendered as a broken image. Nothing leaks when the list changes because
 * React unmounts the old <Thumb>.
 */
function Thumb({ file, alt }: { file: File; alt: string }) {
  const ref = useRef<HTMLImageElement>(null)
  useEffect(() => {
    const url = URL.createObjectURL(file)
    const img = ref.current
    if (img) img.src = url
    return () => {
      if (img) img.removeAttribute('src')
      URL.revokeObjectURL(url)
    }
  }, [file])
  return <img ref={ref} alt={alt} loading="lazy" />
}

/** The project select for a new deployment; hidden when there is only one project to pick. */
function ProjectSelect({ projects, value, onChange, label }: {
  projects: { id: string; name: string }[]
  value: string
  onChange: (id: string) => void
  label: string
}) {
  if (projects.length < 2) return null
  return (
    <select className="triage-select" aria-label={label} value={value} onChange={(e) => onChange(e.target.value)}>
      {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
    </select>
  )
}

function SessionCard({
  session, files, filePaths, groups, projects, onAssign, onCreated, onSkip,
}: {
  session: TriageSession
  files: File[]
  filePaths: string[]
  groups: ReturnType<typeof deploymentPickerGroups>
  projects: { id: string; name: string }[]
  onAssign: (deploymentId: string) => void
  onCreated: (deployment: UploadTarget) => void
  onSkip: () => void
}) {
  const picks = useSamplePicks(session.indices)
  const [creating, setCreating] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [name, setName] = useState(() => defaultDeploymentName(session))
  // Projects can still be loading when this card first renders, so the initial
  // value would stick at '' while the <select> visually shows the first option,
  // and "Create & assign" then failed with "pick a project" on an apparently
  // picked project. Derive the effective value instead of syncing state in an
  // effect (which this repo's lint rejects).
  const [pickedProjectId, setPickedProjectId] = useState('')
  const projectId = pickedProjectId || projects[0]?.id || ''

  const samplePath = filePaths[session.indices[0]] ?? files[session.indices[0]]?.name ?? ''

  const create = async () => {
    if (!name.trim() || !projectId) {
      setError('Give the deployment a name and pick a project.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      onCreated(await createDeployment(projectId, name.trim(), session.exifDeploymentId))
      setCreating(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not create the deployment.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="triage-card">
      <div className="triage-shots">
        {picks.map((i, n) => (
          <Thumb key={i} file={files[i]} alt={`Frame ${n + 1} of this session`} />
        ))}
        {session.indices.length > picks.length && (
          <span className="triage-more">+{session.indices.length - picks.length}</span>
        )}
      </div>

      <dl className="triage-facts">
        <div><dt>Photos</dt><dd>{session.indices.length}</dd></div>
        <div><dt>First → last</dt><dd>{fmt(session.firstMs)} → {fmt(session.lastMs)}</dd></div>
        <div><dt>Duration</dt><dd>{span(session.firstMs, session.lastMs)}</dd></div>
        <div>
          <dt>On the card</dt>
          <dd className="triage-path">
            {session.cardFolder
              ? `MEDIA/${session.cardFolder}${session.folderCount > 1 ? ` (+${session.folderCount - 1} other folder${session.folderCount > 2 ? 's' : ''})` : ''}`
              : samplePath || 'loose files'}
          </dd>
        </div>
        {session.exifDeploymentId && (
          <div>
            <dt>Camera says</dt>
            <dd className="triage-path" title={session.exifDeploymentId}>
              deployment {session.exifDeploymentId.slice(0, 8)}… (not in the database)
            </dd>
          </div>
        )}
      </dl>

      <div className="triage-actions">
        <DeploymentPicker
          groups={groups}
          value=""
          onChange={(id) => { if (id) onAssign(id) }}
          placeholder="Assign to an existing deployment…"
          label="Assign these photos to a deployment"
        />
        <button type="button" className="triage-btn" onClick={() => setCreating((v) => !v)}>
          Create deployment from these photos
        </button>
        <button type="button" className="triage-btn ghost" onClick={onSkip}>
          Skip
        </button>
      </div>

      {creating && (
        <div className="triage-create">
          <label>
            <span>Name</span>
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          {projects.length > 1 && (
            <label>
              <span>Project</span>
              <ProjectSelect projects={projects} value={projectId} onChange={setPickedProjectId} label="Project" />
            </label>
          )}
          {error && <p className="triage-state warn">{error}</p>}
          <button type="button" className="triage-btn primary" disabled={busy} onClick={create}>
            {busy ? 'Creating…' : 'Create & assign'}
          </button>
        </div>
      )}
    </div>
  )
}

/** A decided session, collapsed to one line with a way back. */
function DecidedRow({ session, assignedTo, onChange }: {
  session: TriageSession
  assignedTo: string | null
  onChange: () => void
}) {
  return (
    <li className={session.skipped ? 'is-skipped' : 'is-resolved'}>
      <span className="triage-decided-count">{session.indices.length}</span>
      <span className="triage-decided-when">{fmt(session.firstMs)} → {fmt(session.lastMs)}</span>
      <span className="triage-decided-what">
        {session.skipped ? 'Left out, not uploaded' : `Assigned to ${assignedTo ?? 'a new deployment'}`}
      </span>
      <button type="button" className="triage-btn ghost" onClick={onChange}>Change</button>
    </li>
  )
}

/** "Assign all" and "create one deployment per session", for the sessions still undecided. */
function BulkActions({ pendingCount, groups, projects, busy, progress, error, onAssignAll, onCreateAll }: {
  pendingCount: number
  groups: ReturnType<typeof deploymentPickerGroups>
  projects: { id: string; name: string }[]
  busy: boolean
  progress: string | null
  error: string | null
  onAssignAll: (deploymentId: string) => void
  onCreateAll: (projectId: string) => void
}) {
  const [target, setTarget] = useState('')
  const [pickedProjectId, setPickedProjectId] = useState('')
  const projectId = pickedProjectId || projects[0]?.id || ''

  return (
    <div className="triage-bulk">
      <div className="triage-bulk-row">
        <span>All {plural(pendingCount, 'session')} left:</span>
        <DeploymentPicker
          groups={groups}
          value={target}
          onChange={setTarget}
          placeholder="Pick a deployment…"
          label="Deployment for every session left"
        />
        <button
          type="button"
          className="triage-btn"
          disabled={!target || busy}
          onClick={() => { onAssignAll(target); setTarget('') }}
        >
          Assign all
        </button>
      </div>
      <div className="triage-bulk-row">
        <button
          type="button"
          className="triage-btn"
          disabled={!projectId || busy}
          onClick={() => onCreateAll(projectId)}
        >
          Create one deployment per session
        </button>
        {projects.length > 1 && <span>in</span>}
        <ProjectSelect projects={projects} value={projectId} onChange={setPickedProjectId} label="Project for the new deployments" />
        {progress && <span className="triage-hint" role="status">{progress}</span>}
      </div>
      {error && <p className="triage-state warn">{error}</p>}
    </div>
  )
}

/** How many matched deployments the tally names before "and N more". */
const MATCHED_NAMED = 5

export function UnassignedTriage({
  files, filePaths, exifIds, unresolved, matched = [], blockedCount = 0, deployments, projects,
  onCancel, onDeploymentCreated, onDone,
}: Props) {
  const [sessions, setSessions] = useState<TriageSession[]>(
    () => buildSessions(files, filePaths, unresolved, exifIds),
  )
  // Deployments made on this screen, offered to every other session too.
  const [created, setCreated] = useState<UploadTarget[]>([])
  const [bulkBusy, setBulkBusy] = useState(false)
  const [bulkProgress, setBulkProgress] = useState<string | null>(null)
  const [bulkError, setBulkError] = useState<string | null>(null)

  // The page's list refreshes after a create, so a new deployment can be in both.
  const groups = useMemo(() => {
    const listed = new Set(deployments.map((d) => d.id))
    return deploymentPickerGroups([...deployments, ...created.filter((d) => !listed.has(d.id))], projects)
  }, [deployments, created, projects])
  const labels = useMemo(() => pickerLabels(groups), [groups])

  const assign = (keys: string[], deploymentId: string) => {
    const take = new Set(keys)
    setSessions((prev) => prev.map((s) =>
      take.has(s.key) && !s.deploymentId && !s.skipped ? { ...s, deploymentId, skipped: false } : s))
  }
  const update = (key: string, patch: Partial<TriageSession>) =>
    setSessions((prev) => prev.map((s) => (s.key === key ? { ...s, ...patch } : s)))

  // A new deployment under the camera's id takes every undecided session carrying that id: the
  // id can be created once, and the camera says they belong together.
  const takeCreated = (dep: UploadTarget, key: string, exifDeploymentId: string | null) => {
    setCreated((prev) => [...prev, dep])
    onDeploymentCreated?.()
    const keys: string[] = []
    for (const s of sessions) {
      if (s.key === key || (exifDeploymentId && s.exifDeploymentId === exifDeploymentId)) keys.push(s.key)
    }
    assign(keys, dep.id)
  }

  const createAll = async (projectId: string) => {
    const plans = planBulkCreate(sessions)
    setBulkBusy(true)
    setBulkError(null)
    let done = 0
    setBulkProgress(`Creating ${plans.length} deployment${plans.length === 1 ? '' : 's'}…`)
    let failures: unknown[] = []
    try {
      // Each one assigns its sessions as it lands; a failure leaves its sessions to decide by hand.
      const results = await Promise.allSettled(plans.map(async (plan) => {
        const dep = await createDeployment(projectId, plan.name, plan.exifDeploymentId)
        setCreated((prev) => [...prev, dep])
        assign(plan.sessionKeys, dep.id)
        done += 1
        setBulkProgress(`Created ${done} of ${plans.length}…`)
      }))
      failures = results.flatMap((r) => (r.status === 'rejected' ? [r.reason] : []))
      if (failures.length < plans.length) onDeploymentCreated?.()
    } finally {
      setBulkProgress(null)
      setBulkBusy(false)
    }
    if (failures.length) {
      const why = failures[0] instanceof Error ? failures[0].message : 'Could not create the deployment.'
      setBulkError(`${plural(failures.length, 'deployment')} could not be created (${why}). Decide those sessions one by one.`)
    }
  }

  const matchedCount = matched.reduce((n, m) => n + m.count, 0)
  const assignedCount = sessions.filter((s) => s.deploymentId).reduce((n, s) => n + s.indices.length, 0)
  const skippedCount = sessions.filter((s) => s.skipped).reduce((n, s) => n + s.indices.length, 0)
  const pending = sessions.filter((s) => !s.deploymentId && !s.skipped)
  const decided = sessions.filter((s) => s.deploymentId || s.skipped)
  // What will actually be sent: the photos that matched on their own plus the
  // ones resolved here. Counting only the latter read "0 photos ready" while
  // ten matched photos were about to upload.
  const readyCount = matchedCount + assignedCount
  const leftOut = skippedCount + blockedCount

  const finish = () => {
    const assignments = sessions
      .filter((s): s is TriageSession & { deploymentId: string } => !!s.deploymentId)
      .map((s) => ({ deploymentId: s.deploymentId, indices: s.indices }))
    onDone(assignments, skippedCount)
  }

  const matchedNames = matched.slice(0, MATCHED_NAMED).map((m) => `${m.label} (${m.count})`).join(', ')
  const matchedMore = matched.length > MATCHED_NAMED ? ` and ${matched.length - MATCHED_NAMED} more` : ''

  return (
    <div className="triage">
      <header className="triage-head">
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.375rem' }}>
            <h3>Some photos aren&apos;t linked to a deployment</h3>
            <PageHelp label="About capture sessions" guide="/guides/uploading-photos" guideLabel="Read the upload guide">
              Photos that match no deployment are grouped into capture sessions: the same camera or card
              folder, with no gap over six hours. Pick or create a deployment for each session, or skip
              it; skipped photos are not uploaded.
            </PageHelp>
          </div>
        </div>
        <div className="triage-tally">
          <strong>{sessions.length - pending.length} of {sessions.length}</strong>
          <span>sessions resolved</span>
        </div>
      </header>

      {matched.length > 0 && (
        <p className="triage-matched">
          <strong>{plural(matchedCount, 'photo')} already matched</strong> and will upload as they
          are: {matchedNames}{matchedMore}.
        </p>
      )}
      {blockedCount > 0 && (
        <p className="triage-state warn">
          {plural(blockedCount, 'photo')} {blockedCount === 1 ? 'is' : 'are'} in a project you can&apos;t upload to, so {blockedCount === 1 ? 'it is' : 'they are'} left out.
        </p>
      )}

      {pending.length > 1 && (
        <BulkActions
          pendingCount={pending.length}
          groups={groups}
          projects={projects}
          busy={bulkBusy}
          progress={bulkProgress}
          error={bulkError}
          onAssignAll={(id) => assign(pending.map((s) => s.key), id)}
          onCreateAll={createAll}
        />
      )}

      {pending.length > 0 && (
        <div className="triage-list">
          {pending.map((s) => (
            <SessionCard
              key={s.key}
              session={s}
              files={files}
              filePaths={filePaths}
              groups={groups}
              projects={projects}
              onAssign={(deploymentId) => assign([s.key], deploymentId)}
              onCreated={(dep) => takeCreated(dep, s.key, s.exifDeploymentId)}
              onSkip={() => update(s.key, { skipped: true, deploymentId: null })}
            />
          ))}
        </div>
      )}

      {decided.length > 0 && (
        <ul className="triage-decided" aria-label="Decided sessions">
          {decided.map((s) => (
            <DecidedRow
              key={s.key}
              session={s}
              assignedTo={s.deploymentId ? labels.get(s.deploymentId) ?? null : null}
              onChange={() => update(s.key, { skipped: false, deploymentId: null })}
            />
          ))}
        </ul>
      )}

      <footer className="triage-foot">
        <span className="triage-hint">
          {leftOut > 0
            ? `${plural(readyCount, 'photo')} will upload · ${leftOut} will be left out`
            : `${plural(readyCount, 'photo')} ready to upload`}
        </span>
        <div className="triage-actions">
          <button type="button" className="triage-btn ghost" onClick={onCancel}>Back</button>
          <button
            type="button"
            className="triage-btn primary"
            disabled={pending.length > 0 || readyCount === 0 || bulkBusy}
            onClick={finish}
          >
            {pending.length > 0
              ? `${plural(pending.length, 'session')} still to decide`
              : readyCount === 0 ? 'Nothing to upload' : 'Continue upload'}
          </button>
        </div>
      </footer>
    </div>
  )
}
