import { describe, expect, it } from 'vitest'
import type { JobSummary } from '../types/job'
import { batchWaitText, canStartGrouping, groupingStatusText, pendingGrouping, waitInWords } from './batchWait'

const DEP = 'd0000000-0000-4000-8000-000000000001'
const OTHER = 'd0000000-0000-4000-8000-000000000002'
// 60 minutes of batch wait plus the 30-minute cron, rounded up by the API.
const WAIT = { queued_at: '2026-11-22T01:00:00+00:00', usual_wait_minutes: 120 }

const job = (over: Partial<JobSummary>): JobSummary => ({
  job_id: 'a0000000-0000-4000-8000-000000000001',
  status: 'queued',
  kind: 'ai_embed',
  label: null,
  deployment_ids: [DEP],
  progress: 0,
  summary: null,
  error: null,
  created_at: '2026-11-22T01:00:00+00:00',
  updated_at: null,
  event_count: 0,
  batch_wait: null,
  ...over,
})

describe('batchWaitText', () => {
  it('states the wait the API works out from the settings', () => {
    expect(batchWaitText(WAIT)).toBe('Waiting for the next GPU run, usually within 2 hours')
    expect(batchWaitText({ ...WAIT, usual_wait_minutes: 60 })).toBe('Waiting for the next GPU run, usually within an hour')
    expect(batchWaitText({ ...WAIT, usual_wait_minutes: 45 })).toBe('Waiting for the next GPU run, usually within 45 minutes')
  })

  it('promises no time when the batch is set to start at once', () => {
    expect(batchWaitText({ ...WAIT, usual_wait_minutes: 0 })).toBe('Waiting for the next GPU run')
  })
})

describe('waitInWords', () => {
  it('reads hours when the wait is whole hours, minutes otherwise', () => {
    expect(waitInWords(60)).toBe('an hour')
    expect(waitInWords(120)).toBe('2 hours')
    expect(waitInWords(90)).toBe('90 minutes')
    expect(waitInWords(1)).toBe('a minute')
  })
})

describe('pendingGrouping', () => {
  it('finds the newest unfinished grouping job on the deployment', () => {
    const newest = job({ job_id: 'newest', kind: 'embedding', deployment_ids: [OTHER, DEP] })
    expect(pendingGrouping([newest, job({ job_id: 'older' })], DEP)?.job_id).toBe('newest')
    expect(pendingGrouping([job({ status: 'processing', kind: 'ai_reprocess' })], DEP)).toBeDefined()
  })

  it('ignores finished jobs, other kinds and other deployments', () => {
    expect(pendingGrouping([job({ status: 'completed' }), job({ status: 'failed' })], DEP)).toBeUndefined()
    expect(pendingGrouping([job({ kind: 'ai_pipeline' }), job({ kind: 'upload' })], DEP)).toBeUndefined()
    expect(pendingGrouping([job({ deployment_ids: [OTHER] })], DEP)).toBeUndefined()
    expect(pendingGrouping([job({})], undefined)).toBeUndefined()
  })
})

describe('canStartGrouping', () => {
  it('greys out Run while a run waits for the GPU batch, also after a refresh', () => {
    const waiting = pendingGrouping([job({ batch_wait: WAIT })], DEP)
    expect(canStartGrouping(DEP, false, waiting)).toBe(false)
  })

  it('greys out Run while a run starts or runs, and offers it otherwise', () => {
    expect(canStartGrouping(DEP, true, undefined)).toBe(false)
    expect(canStartGrouping(DEP, false, job({ status: 'processing' }))).toBe(false)
    expect(canStartGrouping(undefined, false, undefined)).toBe(false)
    expect(canStartGrouping(DEP, false, pendingGrouping([job({ status: 'completed' })], DEP))).toBe(true)
  })
})

describe('groupingStatusText', () => {
  it('says a grouping waits for the GPU batch, also after a refresh', () => {
    expect(groupingStatusText(job({ batch_wait: WAIT }), undefined)).toBe('Waiting for the next GPU run, usually within 2 hours.')
    expect(groupingStatusText(job({ batch_wait: WAIT }), 'started-here')).toBe('Waiting for the next GPU run, usually within 2 hours.')
  })

  it("keeps today's wording for work that runs at once", () => {
    expect(groupingStatusText(job({ job_id: 'abcdef1234' }), undefined)).toBe('Queued (job abcdef12). Refresh shortly.')
    expect(groupingStatusText(undefined, '12345678abcd')).toBe('Queued (job 12345678). Refresh shortly.')
  })

  it('says nothing when no grouping is under way', () => {
    expect(groupingStatusText(undefined, undefined)).toBeNull()
  })
})
