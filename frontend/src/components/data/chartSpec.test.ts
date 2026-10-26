import { describe, expect, it } from 'vitest'
import { compile } from 'vega-lite'
import { parse, View } from 'vega'
import { countBarSpec, perDaySpec, typeArcSpec } from './chartSpec'
import { themed } from '../../lib/vegaSpec'
import { detectionsPerDay, summariseObservations } from '../../lib/observationSummary'

const obs = (scientific_name: string, n: number, day = '2026-09-01') =>
  ({ deployment_id: 'd1', scientific_name, observation_type: 'animal', day, n })

const SUMMARY = summariseObservations([obs('Anas platyrhynchos', 22), obs('Rattus norvegicus', 3)])

async function svgOf(spec: Record<string, unknown>): Promise<string> {
  const view = new View(parse(compile(spec as never).spec), { renderer: 'none' })
  return view.toSVG()
}

describe('bar charts carry their counts (#191)', () => {
  it('draws a value label per bar', async () => {
    const spec = countBarSpec(SUMMARY.species, { title: 'Species', italic: true })
    const svg = await svgOf({ ...spec, width: 400, config: themed(spec.config, 'rgb(243, 244, 246)') })

    // One text mark per bar, reading the counts.
    expect(svg).toMatch(/>22</)
    expect(svg).toMatch(/>3</)
    expect(svg).toContain('Anas platyrhynchos')
  })

  it('takes axis and label colours from the page text colour', async () => {
    const spec = countBarSpec(SUMMARY.species, { title: 'Species' })
    const svg = await svgOf({ ...spec, width: 400, config: themed(spec.config, 'rgb(243, 244, 246)') })

    expect(svg).toContain('rgb(243, 244, 246)')
    expect(svg).not.toContain('#555')
  })

  it('leaves the type donut as a single mark', () => {
    expect(typeArcSpec(SUMMARY.types).layer).toBeUndefined()
  })
})

describe('charts read counted rows, not observations (#8)', () => {
  it('hands Vega one row per bar', () => {
    const spec = countBarSpec(SUMMARY.species, { title: 'Species' })
    expect(spec.data.values).toHaveLength(2)
  })

  it('draws detections per day by species', async () => {
    const { rows, species } = detectionsPerDay([
      obs('Anas platyrhynchos', 4, '2026-09-01'),
      obs('Rattus norvegicus', 2, '2026-09-03'),
    ])
    const spec = perDaySpec(rows, species)
    expect(spec.data.values).toHaveLength(2)
    const svg = await svgOf({ ...spec, width: 400, config: themed(spec.config, 'rgb(0, 0, 0)') })
    expect(svg).toContain('Rattus norvegicus')
    expect(svg).toContain('01 Sep')
  })
})
