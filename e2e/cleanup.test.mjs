// Filter and guard logic of cleanup.mjs. No network: run with `npm run test:cleanup`.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  DEV_PROJECT_REF,
  assertDevTarget,
  assertRunTag,
  fixturePrefixes,
  isLorawanTestRow,
  isRunMedia,
  parseRunStart,
  renditionPaths,
} from './cleanup.mjs'

const DEV = `https://${DEV_PROJECT_REF}.supabase.co`
const jwt = (claims) => ['e30', Buffer.from(JSON.stringify(claims)).toString('base64url'), 'sig'].join('.')

test('the target must be the dev project', () => {
  assert.doesNotThrow(() => assertDevTarget(DEV, 'sb_secret_x'))
  assert.doesNotThrow(() => assertDevTarget(`${DEV}/`, jwt({ ref: DEV_PROJECT_REF, role: 'service_role' })))
  for (const url of [
    'https://nuhwmubvygxyddkycmpa.supabase.co',
    `http://${DEV_PROJECT_REF}.supabase.co`,
    `https://${DEV_PROJECT_REF}.supabase.co.evil.example`,
    `https://${DEV_PROJECT_REF}.supabase.co:8443`,
    `https://x.${DEV_PROJECT_REF}.supabase.co`,
    'http://localhost:54321',
    '',
    'not a url',
  ]) {
    assert.throws(() => assertDevTarget(url, 'k'), undefined, url)
  }
  assert.throws(() => assertDevTarget(DEV, ''), /no service-role key/)
  assert.throws(() => assertDevTarget(DEV, jwt({ ref: 'nuhwmubvygxyddkycmpa', role: 'service_role' })), /project nuhwmubvygxyddkycmpa/)
  assert.throws(() => assertDevTarget(DEV, jwt({ ref: DEV_PROJECT_REF, role: 'anon' })), /anon key/)
})

test('the run tag carries the marker and nothing else', () => {
  assert.doesNotThrow(() => assertRunTag('e2e-ci-18342765123-1'))
  assert.doesNotThrow(() => assertRunTag('e2e-local-1760000000000'))
  for (const tag of ['', 'e2e-', 'e2e-%', 'e2e-ci-1,device_eui.neq.x', 'ci-123', 'E2E-CI-1', `e2e-${'a'.repeat(61)}`]) {
    assert.throws(() => assertRunTag(tag), undefined, tag)
  }
})

test('the run start must be recent and in the past', () => {
  const now = new Date('2026-10-10T14:40:00Z')
  assert.equal(parseRunStart('2026-10-10T14:17:00Z', now).toISOString(), '2026-10-10T14:17:00.000Z')
  assert.throws(() => parseRunStart('', now), /not a timestamp/)
  assert.throws(() => parseRunStart('yesterday', now), /not a timestamp/)
  assert.throws(() => parseRunStart('2026-10-10T15:00:00Z', now), /future/)
  assert.throws(() => parseRunStart('2026-10-10T08:00:00Z', now), /6 hours/)
  assert.throws(() => parseRunStart('1970-01-01T00:00:00Z', now), /6 hours/)
})

test('LoRaWAN rows: this run, and marked rows older than an hour', () => {
  const now = new Date('2026-10-10T14:40:00Z')
  const opts = { tag: 'e2e-ci-7-1', now }
  assert.equal(isLorawanTestRow({ device_eui: 'e2e-ci-7-1', received_at: '2026-10-10T14:39:00Z' }, opts), true)
  // The nudge row is dated 2000 on purpose and still belongs to the run.
  assert.equal(isLorawanTestRow({ device_eui: 'e2e-ci-7-1', received_at: '2000-01-01T00:00:00Z' }, opts), true)
  assert.equal(isLorawanTestRow({ device_eui: 'e2e-ci-6-1', received_at: '2026-10-09T14:00:00Z' }, opts), true)
  // Another run in flight, or a developer's local run: too recent to touch.
  assert.equal(isLorawanTestRow({ device_eui: 'e2e-local-1', received_at: '2026-10-10T14:20:00Z' }, opts), false)
  // Real devices, whatever their age.
  assert.equal(isLorawanTestRow({ device_eui: '70B3D57ED0051234', received_at: '2026-01-01T00:00:00Z' }, opts), false)
  assert.equal(isLorawanTestRow({ device_eui: 'xe2e-ci-7-1', received_at: '2026-01-01T00:00:00Z' }, opts), false)
  assert.equal(isLorawanTestRow({ device_eui: null, received_at: '2026-01-01T00:00:00Z' }, opts), false)
  assert.equal(isLorawanTestRow({ device_eui: 'e2e-ci-6-1', received_at: null }, opts), false)
})

test('fixture folders give deployment id prefixes', () => {
  assert.deepEqual(fixturePrefixes(['00000000', '08702E50', '32f55229', 'notes.txt', '1234567', 'IMAGES.000']), ['08702e50', '32f55229'])
})

test('media rows: created this run, on Drive, in a fixture deployment', () => {
  const opts = { startedAt: new Date('2026-10-10T14:17:00Z'), prefixes: ['32f55229', '472e4095'] }
  const row = {
    id: 'm1',
    deployment_id: '32f55229-25b3-4887-a253-b2aae9edec05',
    file_path: 'gdrive://1AbC',
    created_at: '2026-10-10T14:21:07.123456+00:00',
  }
  assert.equal(isRunMedia(row, opts), true)
  assert.equal(isRunMedia({ ...row, deployment_id: '472E4095-1DD9-470D-9FAE-A244AE3EC681' }, opts), true)
  assert.equal(isRunMedia({ ...row, created_at: '2026-10-10T14:16:59.999+00:00' }, opts), false)
  assert.equal(isRunMedia({ ...row, created_at: null }, opts), false)
  assert.equal(isRunMedia({ ...row, file_path: 'https://example.org/a.jpg' }, opts), false)
  assert.equal(isRunMedia({ ...row, file_path: 'x/gdrive://1AbC' }, opts), false)
  assert.equal(isRunMedia({ ...row, deployment_id: 'e0000000-0000-0000-0000-000000000010' }, opts), false)
  assert.equal(isRunMedia({ ...row, deployment_id: null }, opts), false)
  assert.equal(isRunMedia(row, { ...opts, prefixes: [] }), false)
})

test('rendition paths follow the media registry layout', () => {
  assert.deepEqual(renditionPaths({ id: 'm1', deployment_id: 'd1' }), ['thumbnails/d1/m1.jpg', 'previews/d1/m1.jpg', 'crops/d1/m1.jpg'])
})
