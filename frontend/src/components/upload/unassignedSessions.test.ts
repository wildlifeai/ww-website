import { describe, expect, it } from 'vitest'
import { buildSessions, resolutionBreakdown, SESSION_GAP_MS, unresolvedFileIndices, withoutTestPhotos } from './unassignedSessions'

const T0 = Date.UTC(2026, 6, 20, 8, 0, 0) // 20 Jul 2026 08:00Z

function file(name: string, lastModified: number): File {
  return new File([], name, { type: 'image/jpeg', lastModified })
}

function fixture(entries: { path: string; ms: number }[]) {
  const files = entries.map((e, i) => file(e.path.split('/').pop() ?? `f${i}.jpg`, e.ms))
  const paths = entries.map((e) => e.path)
  return { files, paths }
}

describe('buildSessions', () => {
  it('returns nothing when nothing is unresolved', () => {
    const { files, paths } = fixture([{ path: 'MEDIA/AABBCCDD/A0001.JPG', ms: T0 }])
    expect(buildSessions(files, paths, [])).toEqual([])
  })

  it('groups files of one card folder within the gap into one session', () => {
    const { files, paths } = fixture([
      { path: 'MEDIA/AABBCCDD/A0001.JPG', ms: T0 },
      { path: 'MEDIA/AABBCCDD/A0002.JPG', ms: T0 + 60_000 },
      { path: 'MEDIA/AABBCCDD/A0003.JPG', ms: T0 + 120_000 },
    ])
    const sessions = buildSessions(files, paths, [0, 1, 2])
    expect(sessions).toHaveLength(1)
    expect(sessions[0].indices).toEqual([0, 1, 2])
    expect(sessions[0].cardFolder).toBe('AABBCCDD')
    expect(sessions[0].firstMs).toBe(T0)
    expect(sessions[0].lastMs).toBe(T0 + 120_000)
  })

  it('splits a folder into two sessions across a > 6 h gap', () => {
    const { files, paths } = fixture([
      { path: 'MEDIA/AABBCCDD/A0001.JPG', ms: T0 },
      { path: 'MEDIA/AABBCCDD/A0002.JPG', ms: T0 + SESSION_GAP_MS + 1 },
    ])
    const sessions = buildSessions(files, paths, [0, 1])
    expect(sessions).toHaveLength(2)
    expect(sessions[0].indices).toEqual([0])
    expect(sessions[1].indices).toEqual([1])
  })

  it('keeps a gap of exactly the threshold in one session', () => {
    const { files, paths } = fixture([
      { path: 'MEDIA/AABBCCDD/A0001.JPG', ms: T0 },
      { path: 'MEDIA/AABBCCDD/A0002.JPG', ms: T0 + SESSION_GAP_MS },
    ])
    expect(buildSessions(files, paths, [0, 1])).toHaveLength(1)
  })

  it('separates different card folders even at identical times', () => {
    const { files, paths } = fixture([
      { path: 'MEDIA/AABBCCDD/A0001.JPG', ms: T0 },
      { path: 'MEDIA/EEFF0011/B0001.JPG', ms: T0 },
    ])
    const sessions = buildSessions(files, paths, [0, 1])
    expect(sessions).toHaveLength(2)
    expect(new Set(sessions.map((s) => s.cardFolder))).toEqual(new Set(['AABBCCDD', 'EEFF0011']))
  })

  it('normalises folder case and accepts backslash paths', () => {
    const { files, paths } = fixture([
      { path: 'MEDIA\\aabbccdd\\A0001.JPG', ms: T0 },
      { path: 'MEDIA/AABBCCDD/A0002.JPG', ms: T0 + 1000 },
    ])
    const sessions = buildSessions(files, paths, [0, 1])
    expect(sessions).toHaveLength(1)
    expect(sessions[0].cardFolder).toBe('AABBCCDD')
  })

  it('collects loose files (no MEDIA folder) with a null cardFolder', () => {
    const { files, paths } = fixture([{ path: 'IMG_0001.JPG', ms: T0 }])
    const sessions = buildSessions(files, paths, [0])
    expect(sessions).toHaveLength(1)
    expect(sessions[0].cardFolder).toBeNull()
  })

  it('sorts sessions chronologically and orders indices by time within one', () => {
    const { files, paths } = fixture([
      { path: 'MEDIA/EEFF0011/B0001.JPG', ms: T0 + 3_600_000 },
      { path: 'MEDIA/AABBCCDD/A0002.JPG', ms: T0 + 60_000 },
      { path: 'MEDIA/AABBCCDD/A0001.JPG', ms: T0 },
    ])
    const sessions = buildSessions(files, paths, [0, 1, 2])
    expect(sessions.map((s) => s.cardFolder)).toEqual(['AABBCCDD', 'EEFF0011'])
    expect(sessions[0].indices).toEqual([2, 1]) // time order, not input order
  })

  it('only considers the requested unresolved indices', () => {
    const { files, paths } = fixture([
      { path: 'MEDIA/AABBCCDD/A0001.JPG', ms: T0 },
      { path: 'MEDIA/AABBCCDD/A0002.JPG', ms: T0 + 1000 },
    ])
    const sessions = buildSessions(files, paths, [1])
    expect(sessions).toHaveLength(1)
    expect(sessions[0].indices).toEqual([1])
  })

  describe('with EXIF deployment ids (ww-website#140)', () => {
    const DEP = 'e10f7c43-9b90-4f59-bef5-f35b8e698517'

    it('groups by the EXIF id across different card folders', () => {
      // The 2026-09-05 bench run: one frame under MEDIA/00000000/ (folder made
      // before the id was set) carried the same EXIF id as the run's own folder.
      const { files, paths } = fixture([
        { path: 'MEDIA/00000000/IMAGES.000/A9B7AEB0.JPG', ms: T0 },
        { path: 'MEDIA/E10F7C43/IMAGES.000/A9B7AF10.JPG', ms: T0 + 60_000 },
        { path: 'MEDIA/E10F7C43/IMAGES.000/A9B7B560.JPG', ms: T0 + 120_000 },
      ])
      const sessions = buildSessions(files, paths, [0, 1, 2], [DEP, DEP, DEP])
      expect(sessions).toHaveLength(1)
      expect(sessions[0].indices).toEqual([0, 1, 2])
      expect(sessions[0].exifDeploymentId).toBe(DEP)
      // The folder most of the run sits in, not the first one written to.
      expect(sessions[0].cardFolder).toBe('E10F7C43')
      expect(sessions[0].folderCount).toBe(2)
    })

    it('counts one folder for a run that never strayed', () => {
      const { files, paths } = fixture([
        { path: 'MEDIA/AABBCCDD/A0001.JPG', ms: T0 },
        { path: 'MEDIA/AABBCCDD/A0002.JPG', ms: T0 + 1000 },
      ])
      const sessions = buildSessions(files, paths, [0, 1], [DEP, DEP])
      expect(sessions[0].cardFolder).toBe('AABBCCDD')
      expect(sessions[0].folderCount).toBe(1)
    })

    it('separates different EXIF ids inside one folder', () => {
      const other = '11111111-2222-4333-8444-555555555555'
      const { files, paths } = fixture([
        { path: 'MEDIA/AABBCCDD/A0001.JPG', ms: T0 },
        { path: 'MEDIA/AABBCCDD/A0002.JPG', ms: T0 + 1000 },
      ])
      const sessions = buildSessions(files, paths, [0, 1], [DEP, other])
      expect(sessions).toHaveLength(2)
      expect(new Set(sessions.map((s) => s.exifDeploymentId))).toEqual(new Set([DEP, other]))
    })

    it('falls back to the folder for frames with no EXIF id', () => {
      const { files, paths } = fixture([
        { path: 'MEDIA/AABBCCDD/A0001.JPG', ms: T0 },
        { path: 'MEDIA/AABBCCDD/A0002.JPG', ms: T0 + 1000 },
      ])
      const sessions = buildSessions(files, paths, [0, 1], [null, null])
      expect(sessions).toHaveLength(1)
      expect(sessions[0].exifDeploymentId).toBeNull()
      expect(sessions[0].cardFolder).toBe('AABBCCDD')
    })

    it('keeps a frame without an id apart from the run it sits beside', () => {
      const { files, paths } = fixture([
        { path: 'MEDIA/AABBCCDD/A0001.JPG', ms: T0 },
        { path: 'MEDIA/AABBCCDD/A0002.JPG', ms: T0 + 1000 },
      ])
      const sessions = buildSessions(files, paths, [0, 1], [DEP, null])
      expect(sessions).toHaveLength(2)
    })

    it('still splits an EXIF group across a > 6 h gap', () => {
      const { files, paths } = fixture([
        { path: 'MEDIA/AABBCCDD/A0001.JPG', ms: T0 },
        { path: 'MEDIA/AABBCCDD/A0002.JPG', ms: T0 + SESSION_GAP_MS + 1 },
      ])
      expect(buildSessions(files, paths, [0, 1], [DEP, DEP])).toHaveLength(2)
    })

    it('treats a missing exifIds array as no ids', () => {
      const { files, paths } = fixture([{ path: 'MEDIA/AABBCCDD/A0001.JPG', ms: T0 }])
      expect(buildSessions(files, paths, [0])[0].exifDeploymentId).toBeNull()
    })
  })
})

describe('unresolvedFileIndices', () => {
  const KNOWN = '01e03d10-b53a-4d95-b60e-3f2ec26b96ee'
  const deployments = [{ id: KNOWN }]

  it('resolves a frame by its EXIF id even when the card folder matches nothing', () => {
    // The bug behind the "Assign a deployment" panel appearing for four already
    // matched photos: the frames sat in MEDIA/00000000/ (written before the id
    // was configured) while their EXIF named a deployment that does exist.
    const { files, paths } = fixture([
      { path: 'MEDIA/00000000/A0001.JPG', ms: T0 },
      { path: 'MEDIA/00000000/A0002.JPG', ms: T0 + 1000 },
    ])
    expect(unresolvedFileIndices(files, paths, [KNOWN, KNOWN], deployments)).toEqual([])
  })

  it('resolves by folder prefix when a frame carries no EXIF id', () => {
    const { files, paths } = fixture([{ path: 'MEDIA/01E03D10/A0001.JPG', ms: T0 }])
    expect(unresolvedFileIndices(files, paths, [null], deployments)).toEqual([])
  })

  it('is case insensitive on the EXIF id', () => {
    const { files, paths } = fixture([{ path: 'MEDIA/00000000/A0001.JPG', ms: T0 }])
    expect(unresolvedFileIndices(files, paths, [KNOWN.toUpperCase()], deployments)).toEqual([])
  })

  it('returns the frames that match neither', () => {
    const { files, paths } = fixture([
      { path: 'MEDIA/01E03D10/A0001.JPG', ms: T0 },
      { path: 'MEDIA/CEB77C85/B0001.JPG', ms: T0 },
      { path: 'IMG_0001.JPG', ms: T0 },
    ])
    const other = 'ceb77c85-08bd-4868-8b3a-0f0dd4fad62b'
    expect(unresolvedFileIndices(files, paths, [KNOWN, other, null], deployments)).toEqual([1, 2])
  })

  it('treats everything as unresolved when no deployments are loaded', () => {
    const { files, paths } = fixture([{ path: 'MEDIA/01E03D10/A0001.JPG', ms: T0 }])
    expect(unresolvedFileIndices(files, paths, [KNOWN], [])).toEqual([0])
  })

  it('falls back to the folder while the EXIF read is still in flight', () => {
    // exifIds fills asynchronously, so it can be shorter than files.
    const { files, paths } = fixture([
      { path: 'MEDIA/01E03D10/A0001.JPG', ms: T0 },
      { path: 'MEDIA/CEB77C85/B0001.JPG', ms: T0 },
    ])
    expect(unresolvedFileIndices(files, paths, [], deployments)).toEqual([1])
  })
})

describe('withoutTestPhotos (ww-website#287)', () => {
  const REAL = '16bed409-6c1e-4f9b-a3a4-2d1c1e0f9a77'
  const ZERO = '00000000-0000-0000-0000-000000000000'
  const WW500 = 'Wildlife.ai'

  it('drops WW500 frames with no id or the zero id, and keeps a real id in MEDIA/00000000/', () => {
    const { files, paths } = fixture([
      { path: 'MEDIA/00000000/IMAGES.000/A0001.JPG', ms: T0 }, // test photo, no tag
      { path: 'MEDIA/16BED409/IMAGES.000/A0002.JPG', ms: T0 }, // zero id, any folder
      { path: 'MEDIA/00000000/IMAGES.000/A0003.JPG', ms: T0 }, // real tag, stale folder
      { path: 'MEDIA/00000000/IMAGES.000/A0004.BMP', ms: T0 }, // no EXIF at all
      { path: 'DCIM/IMG_0005.JPG', ms: T0 }, // another camera
    ])
    const out = withoutTestPhotos(files, paths, [
      { deploymentId: null, make: WW500 },
      { deploymentId: ZERO, make: WW500 },
      { deploymentId: REAL, make: WW500 },
      { deploymentId: null, make: null },
      { deploymentId: null, make: 'Canon' },
    ])
    expect(out.skipped).toBe(2)
    expect(out.paths).toEqual([
      'MEDIA/00000000/IMAGES.000/A0003.JPG',
      'MEDIA/00000000/IMAGES.000/A0004.BMP',
      'DCIM/IMG_0005.JPG',
    ])
    expect(out.files.map((f) => f.name)).toEqual(['A0003.JPG', 'A0004.BMP', 'IMG_0005.JPG'])
    expect(out.exifIds).toEqual([REAL, null, null])
  })

  it('keeps everything when nothing is a test photo, and tolerates a short exif array', () => {
    const { files, paths } = fixture([{ path: 'MEDIA/00000000/A0001.BMP', ms: T0 }])
    expect(withoutTestPhotos(files, paths, [])).toEqual({ files, paths, exifIds: [null], skipped: 0 })
  })
})

describe('resolutionBreakdown', () => {
  // The 2026-09-05 run-2 capture set: one deployment on the server, two not.
  const KNOWN = '01e03d10-b53a-4d95-b60e-3f2ec26b96ee'
  const MISSING = 'ceb77c85-08bd-4868-8b3a-0f0dd4fad62b'
  const STRAY = '16bed409-192e-48b7-9493-07a47540f144'
  const deployments = [
    { id: KNOWN, project_id: 'p1', location_name: null, name: 'Automated Deployment' },
    { id: 'aabbccdd-0000-4000-8000-000000000000', project_id: 'p1', location_name: 'North Ridge' },
  ]

  it('groups by claim, resolves matched ones and names the rest by prefix, largest first', () => {
    const { files, paths } = fixture([
      ...Array.from({ length: 10 }, (_, n) => ({ path: `MEDIA/01E03D10/A${n}.JPG`, ms: T0 + n })),
      ...Array.from({ length: 6 }, (_, n) => ({ path: `MEDIA/CEB77C85/B${n}.JPG`, ms: T0 + n })),
      { path: 'MEDIA/00000000/C0.JPG', ms: T0 },
      { path: 'MEDIA/00000000/C1.JPG', ms: T0 },
    ])
    const ids = [...Array(10).fill(KNOWN), ...Array(6).fill(MISSING), MISSING, STRAY]
    const rows = resolutionBreakdown(files, paths, ids, deployments, { [MISSING]: 'not_found', [STRAY]: 'not_found' })
    expect(rows.map((r) => [r.label, r.count, r.status])).toEqual([
      ['Automated Deployment', 10, 'matched'],
      ['deployment CEB77C85', 7, 'not_found'],
      ['deployment 16BED409', 1, 'not_found'],
    ])
    expect(rows[0].deploymentId).toBe(KNOWN)
    expect(rows[1].claim).toBe(MISSING)
  })

  it('matches a folder prefix when frames carry no EXIF id, and folds id-less frames into the run', () => {
    const { files, paths } = fixture([
      { path: 'MEDIA/AABBCCDD/A0001.JPG', ms: T0 },
      { path: 'MEDIA/AABBCCDD/A0002.BMP', ms: T0 + 1 },
    ])
    const rows = resolutionBreakdown(files, paths, ['aabbccdd-0000-4000-8000-000000000000', null], deployments, {})
    expect(rows).toHaveLength(1)
    expect(rows[0]).toMatchObject({ label: 'North Ridge', count: 2, status: 'matched' })
  })

  it('reports no_access from the validate verdict and unknown when nothing is claimed', () => {
    const { files, paths } = fixture([
      { path: 'MEDIA/EEFF0011/A0001.JPG', ms: T0 },
      { path: 'IMG_0001.JPG', ms: T0 },
    ])
    const rows = resolutionBreakdown(files, paths, [null, null], deployments, { EEFF0011: 'no_access' })
    expect(rows.map((r) => [r.label, r.status])).toEqual([
      ['deployment EEFF0011', 'no_access'],
      ['no deployment info', 'unknown'],
    ])
  })
})
