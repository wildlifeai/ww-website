import { describe, expect, it } from 'vitest'
import { compile } from 'vega-lite'
import { parse, View } from 'vega'
import { buildVegaSpec, type EnrichedObs } from './chartSpec'
import { themed } from '../../lib/vegaSpec'

const obs = (scientific_name: string, n: number) =>
  Array.from({ length: n }, (_, i) => ({ id: `${scientific_name}${i}`, scientific_name, observation_type: 'animal', location_name: 'Site A', created_at: '2026-09-01' })) as unknown as EnrichedObs[]

const DATA = [...obs('Anas platyrhynchos', 22), ...obs('Rattus norvegicus', 3)]

async function svgOf(spec: Record<string, unknown>): Promise<string> {
  const view = new View(parse(compile(spec as never).spec), { renderer: 'none' })
  return view.toSVG()
}

describe('bar charts carry their counts (#191)', () => {
  it.each(['bar_h', 'bar_v'] as const)('%s draws a value label per bar', async chartType => {
    const spec = buildVegaSpec({ id: 'c', title: 't', chartType, groupBy: 'scientific_name' }, DATA)
    const svg = await svgOf({ ...spec, width: 400, config: themed(spec.config, 'rgb(243, 244, 246)') })

    // One text mark per bar, reading the counts.
    expect(svg).toMatch(/>22</)
    expect(svg).toMatch(/>3</)
  })

  it('takes axis and label colours from the page text colour', async () => {
    const spec = buildVegaSpec({ id: 'c', title: 't', chartType: 'bar_h', groupBy: 'scientific_name' }, DATA)
    const svg = await svgOf({ ...spec, width: 400, config: themed(spec.config, 'rgb(243, 244, 246)') })

    expect(svg).toContain('rgb(243, 244, 246)')
    expect(svg).not.toContain('#555')
  })

  it('leaves pie and line charts as single marks', () => {
    const arc = buildVegaSpec({ id: 'c', title: 't', chartType: 'arc', groupBy: 'scientific_name' }, DATA)
    expect(arc.layer).toBeUndefined()
  })
})
