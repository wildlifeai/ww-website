// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
import { describe, expect, it, vi } from 'vitest'
import { AI_BUSY, AI_NOT_QUEUED, aiRunQueued, aiRunRefusal, aiRunSteps, startAiRun } from './runAiAgain'

describe('aiRunSteps', () => {
  it('runs the crop step with SpeciesNet and keeps pipeline order whatever the click order', () => {
    expect(aiRunSteps(['speciesnet'])).toEqual(['speciesnet', 'animal_crop'])
    expect(aiRunSteps(['bioclip', 'speciesnet'])).toEqual(['speciesnet', 'animal_crop', 'bioclip'])
  })

  it('drops a choice that is not a pipeline model', () => {
    expect(aiRunSteps(['dinov3', 'bioclip'])).toEqual(['bioclip'])
  })
})

describe('startAiRun', () => {
  it('queues the selected photos with the steps and returns how many the job covers', async () => {
    const post = vi.fn().mockResolvedValue({ data: { job_id: 'job-1', status: 'queued', deployment_ids: ['d1'], media_count: 2 } })
    await expect(startAiRun(['m1', 'm2', 'm3'], ['speciesnet'], post)).resolves.toBe(2)
    expect(post).toHaveBeenCalledWith('/api/media/run-selected', { media_ids: ['m1', 'm2', 'm3'], steps: ['speciesnet', 'animal_crop'] })
  })

  it('says clearly that an AI job already holds the deployment', async () => {
    const busy = Object.assign(new Error('An AI run is already in progress on deployment d1. Try again when it finishes.'), { code: 'PIPELINE_BUSY' })
    const post = vi.fn().mockRejectedValue(busy)
    await expect(startAiRun(['m1'], ['speciesnet'], post)).rejects.toThrow(AI_BUSY)
  })

  it('passes any other refusal through, and fails when no job comes back', async () => {
    const post = vi.fn().mockRejectedValue(Object.assign(new Error('You need the Project Member role to change this project.'), { code: 'UNKNOWN' }))
    await expect(startAiRun(['m1'], ['speciesnet'], post)).rejects.toThrow('Project Member')
    await expect(startAiRun(['m1'], ['speciesnet'], vi.fn().mockResolvedValue({ data: {} }))).rejects.toThrow(AI_NOT_QUEUED)
  })
})

describe('messages', () => {
  it('counts the photos in the queued toast', () => {
    expect(aiRunQueued(1)).toMatch(/^AI queued for 1 photo\./)
    expect(aiRunQueued(12)).toMatch(/^AI queued for 12 photos\./)
  })

  it('names the feature switch when the pipeline is off', () => {
    expect(aiRunRefusal({ code: 'FEATURE_DISABLED' })).toMatch(/turned off/)
    expect(aiRunRefusal(null)).toBe(AI_NOT_QUEUED)
  })
})
