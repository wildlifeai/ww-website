#!/usr/bin/env node
/**
 * Deletes what a `full` E2E run left in the dev database and storage. The
 * workflow runs it after every full run, pass or fail (.github/workflows/e2e.yml).
 *
 * Two kinds of leftovers, each found by a marker the run owns:
 *  - LoRaWAN (04): `lorawan_messages` rows whose `device_eui` is the run's tag
 *    (E2E_RUN_TAG). Their `lorawan_parsed_messages` go with them (ON DELETE
 *    CASCADE). Rows with any `e2e-` tag older than an hour are a run that died
 *    before its cleanup, and go too. No real device EUI starts with `e2e-`.
 *  - Upload (03): the backend names media rows itself and records no uploader,
 *    so nothing the spec sends can tag them. A run's media rows are the ones
 *    created since the run started (E2E_RUN_STARTED_AT), on a `gdrive://` path,
 *    in a deployment whose id starts with one of the fixture SD card's folder
 *    names. Full runs never overlap (the workflow's `e2e-full` concurrency
 *    group). Their observations, observation events and renditions in the
 *    `media-renditions` bucket go first. The originals stay in Google Drive: the
 *    dev environment holds no Drive credentials, and Drive dedups them by hash,
 *    so repeated runs do not add copies.
 *
 * Refuses to touch anything but the dev Supabase project.
 *
 * Usage (from e2e/):
 *   SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... E2E_RUN_TAG=e2e-ci-123-1 \
 *   E2E_RUN_STARTED_AT=2026-10-10T14:17:00Z node cleanup.mjs [--dry-run]
 */

import * as fs from 'node:fs'
import * as path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

/** Dev project ref, from documentation/resources/cloud-infrastructure.md. Production is nuhwmubvygxyddkycmpa. */
export const DEV_PROJECT_REF = 'qegeovogqxiouqbrxmnh'
export const MARKER_PREFIX = 'e2e-'
/** Rendition bucket, backend/app/config.py SUPABASE_MEDIA_BUCKET. */
export const RENDITION_BUCKET = 'media-renditions'
const STALE_MARKER_MS = 60 * 60 * 1000
const MAX_RUN_AGE_MS = 6 * 60 * 60 * 1000
const PAGE = 1000
const CHUNK = 100

const HERE = path.dirname(fileURLToPath(import.meta.url))
const FIXTURE_MEDIA = path.resolve(HERE, '../test-fixtures/camera-trap/sdcard/dev-sdcard/MEDIA')

/** Throws unless the URL is the dev project, and, when the key is a JWT, unless it is that project's service role. */
export function assertDevTarget(url, key) {
  let u
  try {
    u = new URL(url)
  } catch {
    throw new Error(`not a URL: ${JSON.stringify(url)}`)
  }
  if (u.protocol !== 'https:' || u.hostname !== `${DEV_PROJECT_REF}.supabase.co` || u.port !== '') {
    throw new Error(`refusing ${u.origin}: cleanup only runs against https://${DEV_PROJECT_REF}.supabase.co`)
  }
  if (!key) throw new Error('no service-role key')
  const parts = key.split('.')
  if (parts.length === 3) {
    let claims
    try {
      claims = JSON.parse(Buffer.from(parts[1], 'base64url').toString('utf8'))
    } catch {
      throw new Error('the key looks like a JWT but its payload does not decode')
    }
    if (claims.ref !== DEV_PROJECT_REF) throw new Error(`refusing a key for project ${claims.ref}`)
    if (claims.role !== 'service_role') throw new Error(`refusing a ${claims.role} key, cleanup needs service_role`)
  }
}

/** Throws unless the tag carries the marker prefix and nothing a filter could misread. */
export function assertRunTag(tag) {
  if (!/^e2e-[a-z0-9-]{3,60}$/.test(tag || '')) {
    throw new Error(`run tag ${JSON.stringify(tag)} must match e2e-[a-z0-9-]{3,60}`)
  }
}

/** The run's start as a Date. Refuses one in the future or older than any run could be. */
export function parseRunStart(value, now = new Date()) {
  const t = new Date(value || '')
  if (Number.isNaN(t.getTime())) throw new Error(`run start ${JSON.stringify(value)} is not a timestamp`)
  if (t.getTime() > now.getTime() + 60_000) throw new Error(`run start ${value} is in the future`)
  if (now.getTime() - t.getTime() > MAX_RUN_AGE_MS) throw new Error(`run start ${value} is more than 6 hours ago`)
  return t
}

/** This run's synthetic telemetry, plus marked rows a dead run left over an hour ago. */
export function isLorawanTestRow(row, { tag, now = new Date() }) {
  const eui = row.device_eui || ''
  if (eui === tag) return true
  if (!eui.startsWith(MARKER_PREFIX)) return false
  const at = new Date(row.received_at || '')
  return !Number.isNaN(at.getTime()) && now.getTime() - at.getTime() > STALE_MARKER_MS
}

/** Lower-cased 8-hex folder names under MEDIA/, the deployment id prefixes the fixture images bind to. */
export function fixturePrefixes(names) {
  return names.map((n) => n.toLowerCase()).filter((n) => /^[0-9a-f]{8}$/.test(n) && n !== '00000000')
}

/** A media row the upload spec created in this run. */
export function isRunMedia(row, { startedAt, prefixes }) {
  if (!prefixes.length) return false
  if (!(row.file_path || '').startsWith('gdrive://')) return false
  const created = new Date(row.created_at || '')
  if (Number.isNaN(created.getTime()) || created < startedAt) return false
  const dep = (row.deployment_id || '').toLowerCase()
  return prefixes.some((p) => dep.startsWith(p))
}

/** Rendition paths the media registry writes for one media row (backend/app/domain/media_registry.py). */
export function renditionPaths(row) {
  const { id, deployment_id: dep } = row
  return [`thumbnails/${dep}/${id}.jpg`, `previews/${dep}/${id}.jpg`, `crops/${dep}/${id}.jpg`]
}

const chunks = (xs, n = CHUNK) => Array.from({ length: Math.ceil(xs.length / n) }, (_, i) => xs.slice(i * n, i * n + n))

async function selectAll(build) {
  const rows = []
  for (let from = 0; ; from += PAGE) {
    const { data, error } = await build().range(from, from + PAGE - 1)
    if (error) throw new Error(error.message)
    rows.push(...(data || []))
    if (!data || data.length < PAGE) return rows
  }
}

async function main() {
  const dryRun = process.argv.includes('--dry-run')
  const url = process.env.SUPABASE_URL || ''
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY || ''
  const tag = process.env.E2E_RUN_TAG || ''
  const now = new Date()

  assertDevTarget(url, key)
  assertRunTag(tag)
  const startedAt = parseRunStart(process.env.E2E_RUN_STARTED_AT, now)

  const { createClient } = await import('@supabase/supabase-js')
  const sb = createClient(url, key, { auth: { persistSession: false } })
  const report = []
  const failures = []
  const step = async (label, fn) => {
    try {
      const n = await fn()
      report.push(`| ${label} | ${n} |`)
    } catch (e) {
      failures.push(`${label}: ${e.message}`)
      report.push(`| ${label} | failed: ${e.message} |`)
    }
  }
  const del = async (table, column, ids) => {
    if (dryRun || !ids.length) return ids.length
    let n = 0
    for (const c of chunks(ids)) {
      const { count, error } = await sb.from(table).delete({ count: 'exact' }).in(column, c)
      if (error) throw new Error(error.message)
      n += count || 0
    }
    return n
  }

  // LoRaWAN: candidates by prefix, kept by the filter above, deleted by id.
  const lora = (
    await selectAll(() => sb.from('lorawan_messages').select('id, device_eui, received_at').like('device_eui', `${MARKER_PREFIX}%`).order('id'))
  ).filter((r) => isLorawanTestRow(r, { tag, now }))
  await step('lorawan_messages (parsed rows cascade)', () => del('lorawan_messages', 'id', lora.map((r) => r.id)))

  // Upload: this run's media rows, then what hangs off them.
  const prefixes = fs.existsSync(FIXTURE_MEDIA) ? fixturePrefixes(fs.readdirSync(FIXTURE_MEDIA)) : []
  const media = prefixes.length
    ? (
        await selectAll(() =>
          sb.from('media').select('id, deployment_id, file_path, created_at').gte('created_at', startedAt.toISOString()).like('file_path', 'gdrive://%').order('id'),
        )
      ).filter((r) => isRunMedia(r, { startedAt, prefixes }))
    : []
  const ids = media.map((r) => r.id)
  // Observations reference media and events by (id, deployment_id) with ON DELETE
  // SET NULL, which would null their NOT NULL deployment_id, so they go first.
  const events = []
  for (const c of chunks(ids)) events.push(...(await selectAll(() => sb.from('observation_events').select('id').in('primary_media_id', c).order('id'))))
  const eventIds = events.map((e) => e.id)
  await step('observations of the media', () => del('observations', 'media_id', ids))
  await step('observations of their events', () => del('observations', 'observation_event_id', eventIds))
  await step('observation_events', () => del('observation_events', 'id', eventIds))
  await step(`${RENDITION_BUCKET} objects`, async () => {
    const paths = []
    for (const m of media) {
      paths.push(...renditionPaths(m))
      const { data } = await sb.storage.from(RENDITION_BUCKET).list(`crops/${m.deployment_id}/${m.id}`)
      for (const f of data || []) paths.push(`crops/${m.deployment_id}/${m.id}/${f.name}`)
    }
    if (dryRun || !paths.length) return paths.length
    let n = 0
    for (const c of chunks(paths)) {
      const { data, error } = await sb.storage.from(RENDITION_BUCKET).remove(c)
      if (error) throw new Error(error.message)
      n += (data || []).length
    }
    return n
  })
  await step('media (assets, embeddings, evidence cascade)', () => del('media', 'id', ids))

  const lines = [
    `### E2E cleanup${dryRun ? ' (dry run)' : ''}`,
    '',
    `Run tag \`${tag}\`, media created since ${startedAt.toISOString()} in deployments ${prefixes.map((p) => `\`${p}\``).join(', ') || '(no fixture folder)'}.`,
    '',
    `| ${dryRun ? 'Would delete' : 'Deleted'} | Count |`,
    '|---|---|',
    ...report,
    '',
  ]
  console.log(lines.join('\n'))
  if (process.env.GITHUB_STEP_SUMMARY) fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY, lines.join('\n') + '\n')
  if (failures.length) {
    for (const f of failures) console.error(`::error::cleanup ${f}`)
    process.exit(1)
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main().catch((e) => {
    console.error(`::error::cleanup refused or failed: ${e.message}`)
    process.exit(2)
  })
}
