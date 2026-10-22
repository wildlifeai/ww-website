/**
 * UploadFlow, the upload surface rendered by /upload-data.
 *
 * Step 1 (idle):       Folder picker / drag-drop zone.
 * Step 2 (images):     Summary tiles, deployment resolution, optional
 *                      assignment panel, optional per-session triage, Upload.
 * Step 2b (camtrapdp): ZIP package summary + inline import progress.
 *
 * Only projects the user can write to take uploads (decision 1 of the 2026-10
 * UI audit: viewers are read-only). The deployment read is scoped to those
 * projects and paged; a frame whose deployment the user can only view is
 * named in "Where these photos will go" and left out of the upload.
 *
 * Clicking "Upload" hands the files to UploadContext.startUpload, which owns
 * the batch loop and the ProgressDock, then lands the user on Annotations
 * filtered to the uploaded deployment. Everything before that moment lives in
 * this component's state, so leaving the page (or reloading it) drops the
 * staged selection; a beforeunload guard warns before that happens.
 *
 * CamtrapDP import is a single synchronous API call; its progress is shown
 * inline and the selection stays until the result is confirmed.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle, ArrowLeft, ArrowRight, Check, ChevronDown, ChevronUp, FolderDown, FolderOpen,
  HardDrive, Images, MapPin, Package, RotateCcw, Upload, X,
} from 'lucide-react'
import { useDragAndDrop } from '../../hooks/useDragAndDrop'
import { apiClient } from '../../lib/apiClient'
import { UnassignedTriage } from './UnassignedTriage'
import {
  BREAKDOWN_SHOWN, buildSessions, cardFolderOf, fileIndicesWithStatus, foldBreakdown, resolutionBreakdown,
  selectForUpload, unresolvedFileIndices, withoutTestPhotos,
} from './unassignedSessions'
import type { ClaimVerdict, ResolutionStatus } from './unassignedSessions'
import { readCameraExif } from '../../lib/exifDeploymentId'
import { supabase } from '../../config/supabase'
import { fetchDeploymentsInProjects } from '../../lib/uploadTargets'
import { useUploadStore, type UploadDeployment, type PendingUpload } from '../../contexts/UploadContext'
import { useProjectSelection } from '../../hooks/useProjectSelection'
import { useProjectRoles } from '../../hooks/useProjectRoles'
import './upload.css'

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────

interface CamtrapImportResult {
  project_id: string
  deployments_imported: number
  media_imported: number
  observations_imported: number
  warnings: string[]
  /** Set when run_ai enqueued a SpeciesNet + Wildlife Brain job for the bundled images. */
  ai_job_id?: string | null
}

// ─────────────────────────────────────────────────────────────────────────────
// CamtrapDP staged-progress labels
// ─────────────────────────────────────────────────────────────────────────────

const CAMTRAP_STAGES: { label: string; pct: number; after: number }[] = [
  { label: 'Uploading package…', pct: 10, after: 500 },
  { label: 'Parsing deployments…', pct: 25, after: 3000 },
  { label: 'Registering taxa via iNat…', pct: 45, after: 8000 },
  { label: 'Importing media records…', pct: 65, after: 15000 },
  { label: 'Importing observations…', pct: 80, after: 22000 },
  { label: 'Finalising import…', pct: 92, after: 30000 },
]

/** What happens to each line of the breakdown, in the user's terms. */
const STATUS_TEXT: Record<ResolutionStatus, string> = {
  matched: 'matched, uploads as it is',
  not_found: 'not in the database, you decide where it goes next',
  no_access: 'in a project you cannot access, will be skipped',
  read_only: "in a project you can't upload to, will be skipped",
  unknown: 'no deployment info, you decide where it goes next',
}

const NO_DEPLOYMENTS: UploadDeployment[] = []

/**
 * Files the image path accepts. Folder drops often leave `type` empty, so the
 * extensions are matched explicitly. Raw .bmp frames are admitted because the
 * backend re-compresses them to JPEG when FF_BMP_INGEST_ENABLED (and ignores
 * them otherwise); they carry no EXIF, so they bind via the card folder.
 */
function isUploadableImage(f: File): boolean {
  const name = f.name.toLowerCase()
  return (
    f.type.startsWith('image/') ||
    name.endsWith('.jpg') ||
    name.endsWith('.jpeg') ||
    name.endsWith('.bmp')
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────────

export function UploadFlow() {
  const { startUpload, phase } = useUploadStore()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  // ── File selection state (lives for as long as the page is mounted) ────────
  const [files, setFiles] = useState<File[]>([])
  const [filePaths, setFilePaths] = useState<string[]>([])
  // The deployment UUID each frame carries in EXIF (0xF200), aligned to `files`.
  // Authoritative over the card folder, which only holds a prefix and can lag the
  // configured id (ww-website#140). Filled asynchronously after selection.
  const [exifIds, setExifIds] = useState<(string | null)[]>([])
  // Progress of that read, or null once it has landed (or nothing is staged).
  const [exifRead, setExifRead] = useState<{ done: number; total: number } | null>(null)
  // WW500 test photos (no deployment set on the camera) dropped from the selection (#287).
  const [testPhotosSkipped, setTestPhotosSkipped] = useState(0)
  // Bumped on every new selection so a read still running for the previous
  // selection cannot write its ids over the new one.
  const selectionRef = useRef(0)
  const [zipFile, setZipFile] = useState<File | null>(null)
  const [selectionError, setSelectionError] = useState<string | null>(null)
  const [showTriage, setShowTriage] = useState(false)
  // Whether to run SpeciesNet + Wildlife Brain after upload (server default: on).
  const [runAi, setRunAi] = useState(true)

  // What /validate and the role check say about claims that matched no deployment the user can
  // upload to, keyed by the claim sent (full EXIF id or card-folder prefix).
  const [claimVerdicts, setClaimVerdicts] = useState<Record<string, ClaimVerdict>>({})
  const [showAllRows, setShowAllRows] = useState(false)

  // Projects the user can upload to: the triage offers their deployments, and files new ones
  // under them. Viewers are read-only (UI audit decision 1).
  const { projects, isLoading: projectsLoading } = useProjectSelection()
  const roles = useProjectRoles()
  const writableProjects = useMemo(
    () => (roles.loading ? [] : projects.filter((p) => roles.canWrite(p.id))),
    [projects, roles],
  )
  const writableIds = useMemo(() => writableProjects.map((p) => p.id).sort(), [writableProjects])
  const targetsReady = !roles.loading && !projectsLoading

  // Every live deployment in those projects, paged past the 1,000-row cap (UI audit 05 #7).
  const deploymentsQuery = useQuery({
    queryKey: ['upload-deployments', writableIds],
    queryFn: async () => {
      const { data, error } = await fetchDeploymentsInProjects<UploadDeployment>(supabase, writableIds)
      if (error) throw new Error(error.message)
      return data ?? []
    },
    enabled: targetsReady && writableIds.length > 0,
    refetchOnWindowFocus: false,
  })
  const deployments = deploymentsQuery.data ?? NO_DEPLOYMENTS

  // CamtrapDP import state
  const [camtrapImporting, setCamtrapImporting] = useState(false)
  const [camtrapResult, setCamtrapResult] = useState<CamtrapImportResult | null>(null)
  const [camtrapError, setCamtrapError] = useState<string | null>(null)
  const [camtrapElapsed, setCamtrapElapsed] = useState(0)
  const [camtrapStage, setCamtrapStage] = useState(0)
  const [showAllWarnings, setShowAllWarnings] = useState(false)
  // CamtrapDP data usually arrives labelled, so AI is opt-in there.
  const [camtrapRunAi, setCamtrapRunAi] = useState(false)

  const folderInputRef = useRef<HTMLInputElement>(null)

  // ── Guard the staged selection against an accidental reload / tab close ───
  // Only while something would actually be lost: staged images not yet handed
  // to startUpload, or a ZIP whose import has not finished.
  const staged = files.length > 0 || camtrapImporting || (zipFile !== null && camtrapResult === null)
  useEffect(() => {
    if (!staged) return
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault() }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [staged])

  // ── CamtrapDP stage timer ──────────────────────────────────────────────────
  // handleCamtrapImport resets the stage and the clock before it starts one.
  useEffect(() => {
    if (!camtrapImporting) return
    const start = Date.now()
    const ticker = setInterval(
      () => setCamtrapElapsed(Math.floor((Date.now() - start) / 1000)),
      1000,
    )
    const timers = CAMTRAP_STAGES.map((s, i) => setTimeout(() => setCamtrapStage(i), s.after))
    return () => { clearInterval(ticker); timers.forEach(clearTimeout) }
  }, [camtrapImporting])

  const clearSelection = () => {
    selectionRef.current += 1
    setFiles([])
    setFilePaths([])
    setExifIds([])
    setExifRead(null)
    setTestPhotosSkipped(0)
    setZipFile(null)
    setSelectionError(null)
    setClaimVerdicts({})
    setShowAllRows(false)
    setCamtrapResult(null)
    setCamtrapError(null)
    setShowTriage(false)
  }

  // ── File processing (routing image vs ZIP) ─────────────────────────────────
  const processFiles = async (incoming: File[]) => {
    clearSelection()
    const token = selectionRef.current
    const zips = incoming.filter((f) => f.name.toLowerCase().endsWith('.zip'))
    const images = incoming.filter(isUploadableImage)

    if (incoming.length > 0 && zips.length === 0 && images.length === 0) {
      setSelectionError('No images or zip files found in the selected folder.')
      return
    }

    if (zips.length > 0 && images.length === 0) {
      setZipFile(zips[0])
      return
    }

    const paths = images.map(
      (f) => (f as File & { entryPath?: string }).entryPath || f.webkitRelativePath || f.name,
    )
    setFiles(images)
    setFilePaths(paths)

    // Read the deployment id the camera stamped into each frame. Reads only the
    // file heads, but a card of a few thousand frames still takes a moment, so
    // the page shows progress and resolves by folder alone until this lands.
    setExifRead({ done: 0, total: images.length })
    const exif = await readCameraExif(images, 8, (done, total) => {
      if (selectionRef.current !== token) return
      if (done === total || done % 25 === 0) setExifRead({ done, total })
    })
    if (selectionRef.current !== token) return

    // WW500 test photos, taken before a deployment was set on the camera, are
    // left out here, before anything is uploaded or offered for assignment (#287).
    const { files: kept, paths: keptPaths, exifIds: ids, skipped } = withoutTestPhotos(images, paths, exif)
    setFiles(kept)
    setFilePaths(keptPaths)
    setTestPhotosSkipped(skipped)
    setExifIds(ids)
    setExifRead(null)

    // Validate what the files claim: full EXIF ids and card-folder prefixes that
    // match none of the deployments the user can upload to. /validate accepts both forms.
    const unknownClaims = (deps: UploadDeployment[]) => {
      const known = new Set(deps.map((d) => d.id.toLowerCase()))
      const unknownExifIds = Array.from(new Set(ids.filter((id): id is string => !!id && !known.has(id))))
      const folderPrefixes = Array.from(new Set(keptPaths.flatMap((p) => cardFolderOf(p) ?? [])))
      const unknownPrefixes = folderPrefixes.filter(
        (id) => !deps.some((d) => d.id.toUpperCase().startsWith(id)),
      )
      return [...unknownExifIds, ...unknownPrefixes]
    }
    let unknown = unknownClaims(deployments)
    // A claim /validate calls valid but that is not in the list is read as a project the user only
    // views, so make sure the list is current first (a deployment made since the page opened).
    let listCurrent = !deploymentsQuery.isError
    if (unknown.length > 0) {
      const fresh = await deploymentsQuery.refetch()
      if (selectionRef.current !== token) return
      listCurrent = !fresh.isError && !!fresh.data
      if (fresh.data) unknown = unknownClaims(fresh.data)
    }

    if (unknown.length > 0) {
      try {
        const res = await apiClient.post('/api/deployments/validate', {
          deployment_ids: unknown,
        })
        if (selectionRef.current !== token) return
        // /validate returns the {prefix: status} map inside the standard {data,...}
        // envelope; tolerate a bare map too (defensive across the split frontend/backend
        // deploy, an old backend still returns it bare).
        const validation: Record<string, 'valid' | 'no_access' | 'not_found'> = res?.data ?? res ?? {}
        const verdicts: Record<string, ClaimVerdict> = {}
        for (const [id, status] of Object.entries(validation)) {
          if (status === 'no_access' || status === 'not_found') verdicts[id] = status
          else if (status === 'valid' && listCurrent) verdicts[id] = 'read_only'
        }
        setClaimVerdicts(verdicts)
      } catch {
        // Non-fatal: proceed without validation feedback
      }
    }
  }

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) processFiles(Array.from(e.target.files))
    // Let the same folder be picked again after "Change selection".
    e.target.value = ''
  }

  const handleCamtrapImport = async () => {
    if (!zipFile) return
    setCamtrapStage(0)
    setCamtrapElapsed(0)
    setCamtrapImporting(true)
    setCamtrapError(null)
    setCamtrapResult(null)
    try {
      const form = new FormData()
      form.append('file', zipFile)
      form.append('run_ai', String(camtrapRunAi))
      const res = await apiClient.upload('/api/camtrapdp/import', form) as {
        data: CamtrapImportResult
      }
      setCamtrapResult(res.data)
    } catch (err: unknown) {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const e = err as any
      const msg =
        e?.response?.data?.detail ??
        e?.response?.data?.error?.message ??
        (e instanceof Error ? e.message : String(e))
      setCamtrapError(msg)
    } finally {
      setCamtrapImporting(false)
    }
  }

  const uploadMode: 'idle' | 'images' | 'camtrapdp' =
    zipFile ? 'camtrapdp' : files.length > 0 ? 'images' : 'idle'

  // Per-deployment fate of the selection, shown before Upload and carried into
  // triage so the photos that already matched are named there too.
  const breakdown = useMemo(
    () => (uploadMode === 'images' ? resolutionBreakdown(files, filePaths, exifIds, deployments, claimVerdicts) : []),
    [uploadMode, files, filePaths, exifIds, deployments, claimVerdicts],
  )
  const matchedRows = breakdown.filter((r) => r.status === 'matched').map((r) => ({ label: r.label, count: r.count }))

  // Frames whose deployment is in a project the user cannot upload to (one they
  // only view, or one they are not in): named in the breakdown as skipped, never
  // offered in triage, never sent.
  const blockedIndices = useMemo(
    () => fileIndicesWithStatus(files, filePaths, exifIds, breakdown, ['read_only', 'no_access']),
    [files, filePaths, exifIds, breakdown],
  )

  // Files whose deployment cannot be resolved from their EXIF id or the card's
  // folder structure. These are the ones the backend would silently drop (no
  // deployment_id -> not stored -> no media row), so they go through triage.
  const unresolvedIndices = useMemo(() => {
    if (uploadMode !== 'images') return []
    const blocked = new Set(blockedIndices)
    return unresolvedFileIndices(files, filePaths, exifIds, deployments).filter((i) => !blocked.has(i))
  }, [files, filePaths, exifIds, deployments, uploadMode, blockedIndices])

  const triageSessions = useMemo(
    () => (unresolvedIndices.length ? buildSessions(files, filePaths, unresolvedIndices, exifIds) : []),
    [files, filePaths, unresolvedIndices, exifIds],
  )

  const handleUpload = async (
    sessionAssignments?: { deploymentId: string; indices: number[] }[],
  ) => {
    if (files.length === 0) return

    // Photos with no resolvable deployment are dropped server-side (the drive
    // job only stores files that have one), so resolve them before uploading.
    // Triage covers every such photo, grouped into capture sessions.
    if (!sessionAssignments && triageSessions.length > 0) {
      setShowTriage(true)
      return
    }

    // Send only what can land: not the blocked frames, and not the triaged
    // sessions left unassigned, which the server would drop anyway.
    const assigned = new Set((sessionAssignments ?? []).flatMap((g) => g.indices))
    const excluded = new Set([...blockedIndices, ...unresolvedIndices.filter((i) => !assigned.has(i))])
    const send = selectForUpload(files, filePaths, excluded, sessionAssignments)
    if (send.files.length === 0) return

    // Resolve which deployment each file belongs to: its triage session, else the
    // EXIF id, else the folder prefix. Powers the annotations redirect + optimistic
    // grid; triaged photos must be in it or the redirect filters to the wrong
    // deployments. A deployment made in triage can also resolve by id once the list
    // refreshes, so each file is counted once.
    const byIndex = new Map<number, string>()
    for (const g of sessionAssignments ?? []) for (const i of g.indices) byIndex.set(i, g.deploymentId)
    const allPending: PendingUpload[] = []
    files.forEach((f, i) => {
      if (excluded.has(i)) return
      const deploymentId = byIndex.get(i) ?? resolveDeploymentId(i)
      if (deploymentId) allPending.push({ fileName: f.name, deploymentId })
    })
    const resolvedDeploymentIds = [...new Set(allPending.map((p) => p.deploymentId))]

    // Images always sync to Google Drive (long-term storage is the default).
    // startUpload copies what it needs before its first await, so the page can
    // drop its staged selection straight away; UploadProvider guards the tab
    // while the batches are still sending.
    startUpload(send.files, send.paths, true, deployments, {
      resolvedDeploymentIds, pending: allPending, sessionAssignments: send.assignments, runAi,
    })
    clearSelection()

    // Land the user on Annotations, filtered to every deployment this upload touched, so they
    // watch their images + AI progress fill in live (banner + optimistic cards). With several the
    // deployment pill reads "N deployments" rather than silently focusing the first.
    if (resolvedDeploymentIds.length > 0) {
      navigate(`/annotations?deployment=${resolvedDeploymentIds.join(',')}`)
    }
  }

  // ── Derived stats ──────────────────────────────────────────────────────────
  const { isDragging, bind } = useDragAndDrop(processFiles)

  // Per-file deployment resolution, EXIF first (exact id), folder prefix second.
  // Returns undefined when neither names a deployment the user can see.
  const resolveDeploymentId = (i: number): string | undefined => {
    const exifId = exifIds[i]
    if (exifId) {
      const hit = deployments.find((d) => d.id.toLowerCase() === exifId)
      if (hit) return hit.id
    }
    const pfx = cardFolderOf(filePaths[i] ?? files[i]?.name ?? '')
    if (pfx) {
      const hit = deployments.find((d) => d.id.toUpperCase().startsWith(pfx))
      if (hit) return hit.id
    }
    return undefined
  }

  // What the selection claims to belong to, for the summary tile and the assignment
  // panel's wording: every distinct EXIF id, plus the folder prefix of frames that carry none.
  const claimedDeployments = new Set<string>()
  files.forEach((f, i) => {
    const claim = exifIds[i] ?? cardFolderOf(filePaths[i] ?? f.name)
    if (claim) claimedDeployments.add(claim.toUpperCase().slice(0, 8))
  })
  const deploymentCount = claimedDeployments.size
  const totalMB = (files.reduce((acc, f) => acc + f.size, 0) / 1024 / 1024).toFixed(1)

  // startUpload refuses to start while a previous batch loop is still sending
  // (busyRef), so say so instead of letting the click do nothing.
  const sending = phase === 'uploading'
  const reading = exifRead !== null
  const sendable = files.length - blockedIndices.length
  const uploadDisabled = reading || sending || sendable === 0
  const uploadLabel = reading
    ? 'Reading photos…'
    : sending
      ? 'Previous upload still sending…'
      : sendable === 0
        ? 'Nothing to upload'
        : `Upload ${sendable} image${sendable !== 1 ? 's' : ''}`
  const shownRows = showAllRows ? breakdown : foldBreakdown(breakdown)

  const testPhotosNote = testPhotosSkipped > 0 && (
    <div className="upload-note" role="status">
      {testPhotosSkipped} test photo{testPhotosSkipped !== 1 ? 's' : ''} skipped (no deployment set on the camera)
    </div>
  )

  // ── Deployment triage (photos the backend would otherwise drop) ──────────
  if (showTriage) {
    return (
      <div className="upload-flow">
        <UnassignedTriage
          files={files}
          filePaths={filePaths}
          exifIds={exifIds}
          unresolved={unresolvedIndices}
          matched={matchedRows}
          blockedCount={blockedIndices.length}
          deployments={deployments}
          projects={writableProjects}
          onCancel={() => setShowTriage(false)}
          onDeploymentCreated={() => queryClient.invalidateQueries({ queryKey: ['upload-deployments'] })}
          onDone={(assignments: { deploymentId: string; indices: number[] }[]) => {
            setShowTriage(false)
            handleUpload(assignments)
          }}
        />
      </div>
    )
  }

  // Nothing role-dependent until the roles and the project list have loaded.
  if (!targetsReady) return null
  // A user who can upload to no project sees one line instead of the drop zone.
  if (writableProjects.length === 0) {
    return (
      <p className="upload-note" role="status">
        {projects.length === 0
          ? 'You are not in a project yet, so there is nowhere to upload to.'
          : 'You can view your projects but not upload to them. Ask a project admin to make you a member.'}
      </p>
    )
  }
  if (deploymentsQuery.isError && deploymentsQuery.data === undefined) {
    return (
      <div className="upload-error" role="alert">
        <AlertTriangle size={14} aria-hidden="true" />
        Couldn&apos;t load your deployments. Reload the page to try again.
      </div>
    )
  }

  return (
    <div className="upload-flow">
      {/* ── Drop zone (idle) ─────────────────────────────────────────────── */}
      {uploadMode === 'idle' && (
        <>
          <div
            {...bind}
            className={`upload-zone${isDragging ? ' is-dragging' : ''}`}
            onClick={() => folderInputRef.current?.click()}
          >
            <div style={{ pointerEvents: 'none' }}>
              <div className="upload-zone-icon">
                {isDragging ? <FolderDown size={40} aria-hidden="true" /> : <FolderOpen size={40} aria-hidden="true" />}
              </div>
              <p style={{ fontWeight: 500, marginBottom: '0.25rem', fontSize: '0.9375rem' }}>
                {isDragging ? 'Drop to select' : 'Click to select a folder or drag & drop it here'}
              </p>
              <p className="upload-zone-hint">
                Wildlife Watcher SD card folder (MEDIA/…) or CamtrapDP .zip package
              </p>
            </div>
            <input
              ref={folderInputRef}
              type="file"
              multiple
              {...({ webkitdirectory: '', directory: '' } as React.InputHTMLAttributes<HTMLInputElement>)}
              style={{ display: 'none' }}
              onChange={handleInputChange}
            />
          </div>
          {selectionError && (
            <div className="upload-error" role="alert">
              <AlertTriangle size={14} aria-hidden="true" /> {selectionError}
            </div>
          )}
          {testPhotosNote}
        </>
      )}

      {/* ── Image mode: stats + options ──────────────────────────────────── */}
      {uploadMode === 'images' && (
        <>
          {/* Summary tiles */}
          <div className="upload-tiles">
            {([
              [Images, files.length, 'images'],
              [MapPin, deploymentCount || '?', 'deployments'],
              [HardDrive, `${totalMB} MB`, 'total size'],
            ] as const).map(([Icon, value, label]) => (
              <div key={label} className="upload-tile">
                <div className="upload-tile-icon"><Icon size={20} aria-hidden="true" /></div>
                <div style={{ fontWeight: 700, fontSize: '1.125rem' }}>{value}</div>
                <div className="upload-tile-label">{label}</div>
              </div>
            ))}
          </div>

          {/* EXIF read in progress: the deployment count above settles when it lands */}
          {exifRead && (
            <div className="upload-reading" role="status">
              Reading photos… {exifRead.done.toLocaleString()} of {exifRead.total.toLocaleString()}
              {' '}(the deployment count may change once every photo has been read)
            </div>
          )}

          {/* Where each group of photos will go, so nothing is a surprise at the triage step */}
          {breakdown.length > 0 && (
            <div className="upload-breakdown">
              <div className="upload-breakdown-title">Where these photos will go</div>
              <ul>
                {shownRows.map((row) => (
                  <li key={row.claim ?? 'none'} className={`is-${row.status}`}>
                    <span className="upload-breakdown-count">{row.count}</span>
                    <span className="upload-breakdown-label" title={row.claim ?? undefined}>{row.label}</span>
                    <span className="upload-breakdown-status">{STATUS_TEXT[row.status]}</span>
                  </li>
                ))}
              </ul>
              {breakdown.length > BREAKDOWN_SHOWN && (
                <button type="button" className="upload-breakdown-more" onClick={() => setShowAllRows((v) => !v)}>
                  {showAllRows ? 'Show fewer' : `Show all ${breakdown.length}`}
                </button>
              )}
            </div>
          )}

          {testPhotosNote}

          {/* AI opt-out: the server runs the pipeline unless told not to */}
          <label className="upload-check">
            <input type="checkbox" checked={runAi} onChange={(e) => setRunAi(e.target.checked)} />
            <span>
              <strong>Run AI analysis after upload</strong>
              <span className="upload-muted"> (SpeciesNet detection, species ID and clustering)</span>
            </span>
          </label>

          {/* Action row */}
          <div className="upload-actions">
            <button type="button" className="upload-btn-secondary" onClick={clearSelection}>
              <ArrowLeft size={14} aria-hidden="true" /> Change selection
            </button>
            <button
              type="button"
              className="btn upload-btn-primary"
              onClick={() => handleUpload()}
              disabled={uploadDisabled}
            >
              {!uploadDisabled && <Upload size={16} aria-hidden="true" />}
              {uploadLabel}
            </button>
          </div>
        </>
      )}

      {/* ── CamtrapDP mode ───────────────────────────────────────────────── */}
      {uploadMode === 'camtrapdp' && zipFile && (
        <>
          {/* Package summary */}
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '0.875rem 1rem',
            border: '1px solid var(--border)', borderRadius: 'var(--radius)',
            backgroundColor: 'var(--surface)',
          }}>
            <div>
              <div style={{ fontWeight: 600, display: 'flex', alignItems: 'center', gap: '0.375rem' }}>
                <Package size={16} aria-hidden="true" /> CamtrapDP package
              </div>
              <div className="upload-muted" style={{ fontSize: '0.8125rem', marginTop: '0.2rem' }}>
                {zipFile.name} · {(zipFile.size / 1024).toFixed(1)} KB
              </div>
            </div>
            {!camtrapImporting && !camtrapResult && (
              <button type="button" className="upload-btn-secondary" onClick={clearSelection}>
                <X size={14} aria-hidden="true" /> Clear
              </button>
            )}
          </div>

          <p className="upload-note" style={{ margin: 0 }}>
            Importing creates a new project with the package&apos;s deployments, photos and observations.
          </p>

          {/* Import button */}
          {!camtrapResult && !camtrapImporting && !camtrapError && (
            <>
              <label className="upload-check">
                <input type="checkbox" checked={camtrapRunAi} onChange={(e) => setCamtrapRunAi(e.target.checked)} />
                <span>
                  <strong>Run AI analysis after import</strong>
                  <span className="upload-muted"> (off by default: CamtrapDP data usually arrives already labelled)</span>
                </span>
              </label>
              <button type="button" className="btn upload-btn-primary" onClick={handleCamtrapImport} style={{ alignSelf: 'flex-start' }}>
                <Upload size={16} aria-hidden="true" /> Import CamtrapDP package
              </button>
            </>
          )}

          {/* In-progress */}
          {camtrapImporting && (
            <div style={{
              padding: '1rem', borderRadius: 'var(--radius)',
              backgroundColor: 'var(--surface)', border: '1px solid var(--border)',
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.5rem' }}>
                <span style={{ fontSize: '0.8125rem', fontWeight: 500 }}>
                  {CAMTRAP_STAGES[camtrapStage]?.label ?? 'Importing…'}
                </span>
                <span className="upload-muted" style={{ fontSize: '0.75rem', fontVariantNumeric: 'tabular-nums' }}>
                  {camtrapElapsed}s
                </span>
              </div>
              <div style={{ height: 6, backgroundColor: 'var(--border)', borderRadius: 3, overflow: 'hidden' }}>
                <div style={{
                  height: '100%',
                  width: `${CAMTRAP_STAGES[camtrapStage]?.pct ?? 10}%`,
                  borderRadius: 3,
                  transition: 'width 1.2s ease',
                  backgroundImage: 'linear-gradient(90deg, var(--primary), #66bb6a)',
                }} />
              </div>
              <p className="upload-muted" style={{ fontSize: '0.75rem', margin: '0.5rem 0 0 0' }}>
                Large packages can take 30 to 60 s. Please keep this tab open.
              </p>
            </div>
          )}

          {/* Error */}
          {camtrapError && (
            <>
              <div className="upload-error" role="alert">
                <AlertTriangle size={14} aria-hidden="true" /> {camtrapError}
              </div>
              <button type="button" className="btn upload-btn-primary" onClick={handleCamtrapImport} style={{ alignSelf: 'flex-start' }}>
                <RotateCcw size={16} aria-hidden="true" /> Retry import
              </button>
            </>
          )}

          {/* Success */}
          {camtrapResult && (
            <div style={{
              padding: '1rem', borderRadius: 'var(--radius)',
              backgroundColor: 'rgba(76,175,80,0.07)',
              border: '1px solid rgba(76,175,80,0.3)',
              fontSize: '0.8125rem',
            }}>
              <div style={{ fontWeight: 600, marginBottom: '0.625rem', color: 'var(--primary)', display: 'flex', alignItems: 'center', gap: '0.375rem' }}>
                <Check size={16} aria-hidden="true" /> Import successful
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '0.5rem', marginBottom: '0.75rem' }}>
                {([
                  ['Deployments', camtrapResult.deployments_imported],
                  ['Media records', camtrapResult.media_imported],
                  ['Observations', camtrapResult.observations_imported],
                ] as [string, number][]).map(([label, count]) => (
                  <div
                    key={label}
                    style={{ textAlign: 'center', padding: '0.375rem', backgroundColor: 'var(--surface)', borderRadius: 'var(--radius)' }}
                  >
                    <div style={{ fontSize: '1.125rem', fontWeight: 700 }}>{count}</div>
                    <div className="upload-muted" style={{ fontSize: '0.75rem' }}>{label}</div>
                  </div>
                ))}
              </div>
              {camtrapResult.warnings.length > 0 && (
                <div style={{ marginBottom: '0.5rem' }}>
                  <strong>Warnings ({camtrapResult.warnings.length}):</strong>
                  <ul style={{ margin: '0.25rem 0 0 1rem', padding: 0 }}>
                    {(showAllWarnings
                      ? camtrapResult.warnings
                      : camtrapResult.warnings.slice(0, 3)
                    ).map((w, i) => (
                      <li key={i} style={{ marginBottom: '0.2rem', wordBreak: 'break-word' }}>{w}</li>
                    ))}
                  </ul>
                  {camtrapResult.warnings.length > 3 && (
                    <button
                      type="button"
                      onClick={() => setShowAllWarnings((v) => !v)}
                      style={{ marginTop: '0.35rem', background: 'none', border: 'none', cursor: 'pointer', color: 'var(--primary)', fontSize: '0.75rem', padding: 0, display: 'inline-flex', alignItems: 'center', gap: '0.25rem' }}
                    >
                      {showAllWarnings
                        ? <><ChevronUp size={12} aria-hidden="true" /> Show fewer</>
                        : <><ChevronDown size={12} aria-hidden="true" /> Show all {camtrapResult.warnings.length}</>}
                    </button>
                  )}
                </div>
              )}
              {camtrapResult.ai_job_id && (
                <div style={{ marginBottom: '0.5rem' }}>
                  AI analysis queued for the bundled images. Track it in{' '}
                  <Link to="/processing" style={{ color: 'var(--primary)' }}>Processing history</Link>.
                </div>
              )}
              <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center', marginTop: '0.25rem' }}>
                <Link
                  to="/annotations"
                  style={{ color: 'var(--primary)', fontWeight: 500, textDecoration: 'none', display: 'inline-flex', alignItems: 'center', gap: '0.25rem' }}
                >
                  View in Media <ArrowRight size={14} aria-hidden="true" />
                </Link>
                <button type="button" className="upload-btn-secondary" onClick={clearSelection}>Import another</button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}
