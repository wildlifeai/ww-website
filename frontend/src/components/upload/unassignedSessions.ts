/**
 * Capture-session grouping for photos that arrive without a deployment.
 *
 * Kept apart from the component so both UploadFlow (which needs to know whether
 * triage is required) and the triage screen itself can use it without breaking
 * fast refresh.
 */
import { isTestPhoto, type CameraExif } from '../../lib/exifDeploymentId'

/** A gap longer than this starts a new capture session. */
export const SESSION_GAP_MS = 6 * 60 * 60 * 1000

export interface TriageDeployment {
  id: string
  project_id: string
  location_name: string | null
  name?: string | null
  deployment_start?: string | null
  timezone?: string | null
}

/** A deployment's display name: the location, else the row name, else the id prefix. */
export function deploymentLabel(d: TriageDeployment): string {
  return d.location_name || d.name || d.id.slice(0, 8)
}

export interface TriageSession {
  key: string
  /** Indices into the original files array. */
  indices: number[]
  firstMs: number
  lastMs: number
  /** The MEDIA/<prefix> folder most of the session's frames sit in, when any path carries one. */
  cardFolder: string | null
  /** How many distinct card folders the session spans (a run regrouped by EXIF id can span two). */
  folderCount: number
  /**
   * The deployment UUID the camera stamped into these frames' EXIF (0xF200),
   * when present. Authoritative over the folder: it names the deployment the
   * device was configured with, and the folder can lag it (ww-website#140).
   */
  exifDeploymentId: string | null
  /** Chosen deployment id, or null while unresolved. */
  deploymentId: string | null
  skipped: boolean
}

export function fmt(ms: number): string {
  return new Date(ms).toLocaleString(undefined, {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
  })
}

export function span(a: number, b: number): string {
  const mins = Math.max(1, Math.round((b - a) / 60000))
  if (mins < 60) return `${mins} m`
  const h = Math.floor(mins / 60)
  return `${h} h ${mins % 60} m`
}

/** The MEDIA/<8-hex>/ folder in a card path, upper-cased, or null. */
export function cardFolderOf(path: string): string | null {
  const m = path.match(/MEDIA[/\\]([A-Fa-f0-9]{8})[/\\]/i)
  return m ? m[1].toUpperCase() : null
}

/**
 * Drop WW500 test photos (no deployment set on the camera, see isTestPhoto)
 * from a selection, keeping `paths` and the EXIF deployment ids aligned to the
 * files that remain. The folder plays no part: a frame in MEDIA/00000000/ that
 * carries a real id stays (ww-website#287).
 */
export function withoutTestPhotos(
  files: File[],
  paths: string[],
  exif: CameraExif[],
): { files: File[]; paths: string[]; exifIds: (string | null)[]; skipped: number } {
  const out = { files: [] as File[], paths: [] as string[], exifIds: [] as (string | null)[], skipped: 0 }
  files.forEach((f, i) => {
    const e = exif[i] ?? { deploymentId: null, make: null }
    if (isTestPhoto(e)) {
      out.skipped += 1
      return
    }
    out.files.push(f)
    out.paths.push(paths[i])
    out.exifIds.push(e.deploymentId)
  })
  return out
}

/**
 * Group unresolved files into capture sessions, by the EXIF deployment id when
 * the frames carry one, else by card folder, then by time gaps within a group.
 *
 * Grouping by the EXIF id first is what puts a frame that landed in the wrong
 * folder (MEDIA/00000000/, created before the id was set) back with its run.
 */
export function buildSessions(
  files: File[],
  filePaths: string[],
  unresolved: number[],
  exifIds: (string | null)[] = [],
): TriageSession[] {
  const rows = unresolved.map((i) => {
    const path = filePaths[i] ?? files[i].name
    return { i, ms: files[i].lastModified || 0, folder: cardFolderOf(path), exifId: exifIds[i] ?? null }
  })

  const byGroup = new Map<string, typeof rows>()
  for (const r of rows) {
    const k = r.exifId ? `exif:${r.exifId}` : r.folder ? `folder:${r.folder}` : '—'
    if (!byGroup.has(k)) byGroup.set(k, [])
    byGroup.get(k)!.push(r)
  }

  const out: TriageSession[] = []
  for (const [group, list] of byGroup) {
    list.sort((a, b) => a.ms - b.ms)
    let cur: typeof list = []
    const flush = () => {
      if (!cur.length) return
      const exifDeploymentId = group.startsWith('exif:') ? group.slice(5) : null
      // With an EXIF id the folder is informational and may be mixed (a frame
      // written to MEDIA/00000000/ before the id was set sits beside the run's
      // own folder), so name the folder most of the frames are in.
      const folderTally = new Map<string, number>()
      for (const r of cur) if (r.folder) folderTally.set(r.folder, (folderTally.get(r.folder) ?? 0) + 1)
      let cardFolder: string | null = null
      for (const [folder, n] of folderTally) {
        if (cardFolder === null || n > (folderTally.get(cardFolder) ?? 0)) cardFolder = folder
      }
      out.push({
        key: `${group}-${out.length}`,
        indices: cur.map((r) => r.i),
        firstMs: cur[0].ms,
        lastMs: cur[cur.length - 1].ms,
        cardFolder,
        folderCount: folderTally.size,
        exifDeploymentId,
        deploymentId: null,
        skipped: false,
      })
      cur = []
    }
    for (const r of list) {
      if (cur.length && r.ms - cur[cur.length - 1].ms > SESSION_GAP_MS) flush()
      cur.push(r)
    }
    flush()
  }
  return out.sort((a, b) => a.firstMs - b.firstMs)
}

/**
 * Indices of files that resolve to no deployment the user can see, from their
 * EXIF id (exact) or their card folder (prefix). These are the ones the backend
 * would silently drop, because the drive-upload job only stores files that carry
 * a deployment_id, so they are exactly the set triage has to resolve.
 *
 * The EXIF id wins, so a frame whose folder prefix matches nothing is still
 * resolved when its id does: the card folder is created at boot, before the
 * deployment id is configured, and a run can carry frames from more than one
 * (ww-website#140).
 */
export function unresolvedFileIndices(
  files: File[],
  filePaths: string[],
  exifIds: (string | null)[],
  deployments: { id: string }[],
): number[] {
  const knownFull = new Set(deployments.map((d) => d.id.toLowerCase()))
  const knownPrefix = new Set(deployments.map((d) => d.id.slice(0, 8).toUpperCase()))
  const out: number[] = []
  files.forEach((f, i) => {
    const exifId = exifIds[i]
    if (exifId && knownFull.has(exifId.toLowerCase())) return
    const pfx = cardFolderOf(filePaths[i] ?? f.name)
    if (pfx && knownPrefix.has(pfx)) return
    out.push(i)
  })
  return out
}

/**
 * What /api/deployments/validate and the role check say about a claim that matched no deployment
 * the user can upload to: it does not exist, it is in a project the user is not in, or the user
 * can see it but only as a viewer (decision 1 of the 2026-10 UI audit: viewers do not upload).
 */
export type ClaimVerdict = 'not_found' | 'no_access' | 'read_only'

export type ResolutionStatus = 'matched' | 'unknown' | ClaimVerdict

/** One line of the "where these photos will go" summary: a claimed deployment and its fate. */
export interface ResolutionRow {
  /** What the frames claim: the full EXIF id, else the card-folder prefix, else null. */
  claim: string | null
  /** The deployment the claim resolved to, when the user can see it. */
  deploymentId: string | null
  label: string
  count: number
  status: ResolutionStatus
}

/** What a frame claims to belong to: its EXIF deployment id, else its card folder, else null. */
export function fileClaim(file: File, path: string | undefined, exifId: string | null | undefined): string | null {
  return exifId ?? cardFolderOf(path ?? file.name) ?? null
}

/** The shared 8-hex prefix a claim is grouped under, so a run's EXIF frames and its id-less BMP frames meet. */
export function claimKey(claim: string | null): string {
  return claim ? claim.toUpperCase().slice(0, 8) : ''
}

/**
 * Per-deployment breakdown of a staged selection, so the user sees before
 * uploading which photos already match a deployment (and upload as they are)
 * and which will need a decision. Frames are grouped by what they claim (EXIF
 * id first, folder prefix second, keyed on the shared 8-hex prefix so a run's
 * EXIF frames and its id-less BMP frames land on one line); `invalid` is the
 * /api/deployments/validate verdict for claims that matched no visible
 * deployment. Sorted largest group first.
 */
export function resolutionBreakdown(
  files: File[],
  filePaths: string[],
  exifIds: (string | null)[],
  deployments: TriageDeployment[],
  invalid: Record<string, ClaimVerdict>,
): ResolutionRow[] {
  const groups = new Map<string, { claim: string | null; count: number }>()
  files.forEach((f, i) => {
    const claim = fileClaim(f, filePaths[i], exifIds[i])
    const key = claimKey(claim)
    const g = groups.get(key)
    if (g) {
      g.count += 1
      // Prefer the full id over a bare prefix when the group has both.
      if (claim && (!g.claim || claim.length > g.claim.length)) g.claim = claim
    } else {
      groups.set(key, { claim, count: 1 })
    }
  })

  const rows: ResolutionRow[] = []
  for (const [key, g] of groups) {
    const dep = g.claim
      ? deployments.find((d) => d.id.toLowerCase() === g.claim!.toLowerCase())
        ?? deployments.find((d) => d.id.toUpperCase().startsWith(key))
      : undefined
    let status: ResolutionStatus
    if (dep) status = 'matched'
    else if (!g.claim) status = 'unknown'
    else status = invalid[g.claim] ?? invalid[key] ?? 'unknown'
    rows.push({
      claim: g.claim,
      deploymentId: dep?.id ?? null,
      label: dep ? deploymentLabel(dep) : g.claim ? `deployment ${key}` : 'no deployment info',
      count: g.count,
      status,
    })
  }
  return rows.sort((a, b) => b.count - a.count || a.label.localeCompare(b.label))
}

/** "Where these photos will go" lines shown before "Show all". */
export const BREAKDOWN_SHOWN = 8

/**
 * The lines shown while the breakdown is folded: every line that needs attention
 * (anything not matched), then the largest matched lines up to BREAKDOWN_SHOWN, in
 * the breakdown's order.
 */
export function foldBreakdown(rows: ResolutionRow[]): ResolutionRow[] {
  if (rows.length <= BREAKDOWN_SHOWN) return rows
  const attention = rows.filter((r) => r.status !== 'matched')
  const room = Math.max(0, BREAKDOWN_SHOWN - attention.length)
  const keep = new Set([...attention, ...rows.filter((r) => r.status === 'matched').slice(0, room)])
  return rows.filter((r) => keep.has(r))
}

/**
 * Indices of the frames whose breakdown line has one of `statuses`, for example the ones in a
 * project the user cannot upload to, which are left out. A frame belongs to the line of its
 * claim's 8-hex key, as in `resolutionBreakdown`.
 */
export function fileIndicesWithStatus(
  files: File[],
  filePaths: string[],
  exifIds: (string | null)[],
  rows: ResolutionRow[],
  statuses: ResolutionStatus[],
): number[] {
  const wanted = new Set(statuses)
  const keys = new Set<string>()
  for (const r of rows) if (r.claim && wanted.has(r.status)) keys.add(claimKey(r.claim))
  if (keys.size === 0) return []
  const out: number[] = []
  files.forEach((f, i) => {
    if (keys.has(claimKey(fileClaim(f, filePaths[i], exifIds[i])))) out.push(i)
  })
  return out
}

/** The name a new deployment made from a session starts with: the camera's id, else the card folder, else the date. */
export function defaultDeploymentName(session: TriageSession): string {
  if (session.exifDeploymentId) return `Camera deployment ${session.exifDeploymentId.slice(0, 8)}`
  if (session.cardFolder) return `Card ${session.cardFolder}`
  return `Photos from ${fmt(session.firstMs)}`
}

/** One deployment "create one deployment per session" makes, and the sessions it takes. */
export interface BulkCreatePlan {
  name: string
  /** Create the row under the id the camera stamped, so the phone's later sync converges on it. */
  exifDeploymentId: string | null
  sessionKeys: string[]
}

/**
 * The deployments "create one deployment per session" makes for the sessions still undecided.
 * Sessions that carry the same camera id (one run split by a long gap) share one deployment:
 * the id can be created only once, and the camera says they belong together.
 */
export function planBulkCreate(sessions: TriageSession[]): BulkCreatePlan[] {
  const out: BulkCreatePlan[] = []
  const byExifId = new Map<string, BulkCreatePlan>()
  for (const s of sessions) {
    if (s.deploymentId || s.skipped) continue
    const existing = s.exifDeploymentId ? byExifId.get(s.exifDeploymentId) : undefined
    if (existing) {
      existing.sessionKeys.push(s.key)
      continue
    }
    const plan: BulkCreatePlan = { name: defaultDeploymentName(s), exifDeploymentId: s.exifDeploymentId, sessionKeys: [s.key] }
    if (s.exifDeploymentId) byExifId.set(s.exifDeploymentId, plan)
    out.push(plan)
  }
  return out
}

/**
 * The files an upload sends: every staged file except `exclude` (read-only frames, and triaged
 * sessions left unassigned, which the server would only drop), with the session assignments
 * re-indexed to the kept list.
 */
export function selectForUpload<T>(
  files: T[],
  paths: string[],
  exclude: Iterable<number>,
  assignments: { deploymentId: string; indices: number[] }[] = [],
): { files: T[]; paths: string[]; assignments: { deploymentId: string; indices: number[] }[] } {
  const drop = new Set(exclude)
  const newIndex = new Map<number, number>()
  const kept: T[] = []
  const keptPaths: string[] = []
  files.forEach((f, i) => {
    if (drop.has(i)) return
    newIndex.set(i, kept.length)
    kept.push(f)
    keptPaths.push(paths[i] ?? '')
  })
  const remapped: { deploymentId: string; indices: number[] }[] = []
  for (const g of assignments) {
    const indices = g.indices.flatMap((i) => (newIndex.has(i) ? [newIndex.get(i)!] : []))
    if (indices.length > 0) remapped.push({ deploymentId: g.deploymentId, indices })
  }
  return { files: kept, paths: keptPaths, assignments: remapped }
}
