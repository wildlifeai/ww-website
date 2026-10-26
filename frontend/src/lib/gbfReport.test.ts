import { describe, expect, it, vi } from 'vitest'
import {
  defaultPeriod, fetchGbfReport, gbfCsv, gbfFilename, gbfReportPath, periodProblem, speciesLabel, targetUrl,
  type GbfReport, type GbfSpecies,
} from './gbfReport'

const rat: GbfSpecies = {
  taxon_id: 't-rat',
  scientific_name: 'Rattus rattus',
  common_name: 'Ship rat',
  conservation_status: 'Least Concern',
  invasive: true,
  detections: 3,
  rate: 15.79,
  sites_with: 2,
  monthly: [{ month: '2026-01', detections: 3, rate: 15.79 }],
  first_detections: [{ deployment_id: 'd2', deployment_name: 'Stream', date: '2026-01-08' }],
}

const report: GbfReport = {
  method_version: 'gbf-1',
  period: { from: '2026-01-01', to: '2026-01-31' },
  projects: [{ id: 'p2', name: '=cmd' }, { id: 'p1', name: 'Alpha, north' }],
  camera_nights_by_month: [{ month: '2026-01', camera_nights: 19 }],
  target_6: { sites_monitored: 2, species_recorded: 1, species: [rat] },
  target_4: { sites_monitored: 2, species_recorded: 0, species: [] },
  target_20: { cameras: 2, sites: 2, camera_nights: 19, photos: 40, photos_with_ai: 38, photos_reviewed: 5 },
  target_21: null,
  target_22: { reviewers: 2 },
}

describe('defaultPeriod', () => {
  it('is the last 12 months, today included', () => {
    expect(defaultPeriod(new Date(2026, 9, 25))).toEqual({ from: '2025-10-26', to: '2026-10-25' })
    expect(defaultPeriod(new Date(2026, 11, 31))).toEqual({ from: '2026-01-01', to: '2026-12-31' })
  })
})

describe('periodProblem', () => {
  it('needs both dates in order', () => {
    expect(periodProblem('2026-01-01', '2026-01-31')).toBeNull()
    expect(periodProblem('2026-01-01', '2026-01-01')).toBeNull()
    expect(periodProblem('', '2026-01-31')).toMatch(/start and an end/)
    expect(periodProblem('2026-02-01', '2026-01-31')).toMatch(/after/)
  })
})

describe('the request', () => {
  it('names the projects and the period', () => {
    expect(gbfReportPath(['p1', 'p2'], '2026-01-01', '2026-01-31'))
      .toBe('/api/reports/gbf?project_ids=p1%2Cp2&from=2026-01-01&to=2026-01-31')
  })

  it('returns the data of the envelope', async () => {
    const get = vi.fn().mockResolvedValue({ data: report })
    await expect(fetchGbfReport(get, ['p1'], '2026-01-01', '2026-01-31')).resolves.toBe(report)
    expect(get).toHaveBeenCalledWith('/api/reports/gbf?project_ids=p1&from=2026-01-01&to=2026-01-31')
    await expect(fetchGbfReport(vi.fn().mockResolvedValue({}), ['p1'], 'a', 'b')).rejects.toThrow()
  })
})

describe('labels', () => {
  it('names a species and a target', () => {
    expect(speciesLabel(rat)).toBe('Ship rat (Rattus rattus)')
    expect(speciesLabel({ common_name: null, scientific_name: 'Rattus rattus' })).toBe('Rattus rattus')
    expect(targetUrl(6)).toBe('https://www.cbd.int/gbf/targets/6')
    expect(gbfFilename(report, 'csv')).toBe('gbf-report-2026-01-01-to-2026-01-31.csv')
  })
})

describe('gbfCsv', () => {
  const lines = gbfCsv(report).trimEnd().split('\n')

  it('starts with the period and the projects', () => {
    expect(lines.slice(0, 5)).toEqual([
      'report,GBF contribution',
      'period_from,2026-01-01',
      'period_to,2026-01-31',
      // Quoted for the comma; a leading = is defused so a spreadsheet does not run it.
      `projects,"'=cmd; Alpha, north"`,
      'method_version,gbf-1',
    ])
    expect(lines[5]).toMatch(/^note,"Site-level evidence/)
    expect(lines[6]).toBe('')
    expect(lines[7]).toBe('target,subject,metric,month,value')
  })

  it('has one figure per row', () => {
    const table = lines.slice(8)
    expect(table).toContain('6,,sites_monitored,,2')
    expect(table).toContain('6,Rattus rattus,detections,,3')
    expect(table).toContain('6,Rattus rattus,rate_per_100_camera_nights,,15.79')
    expect(table).toContain('6,Rattus rattus,sites_with,,2')
    expect(table).toContain('6,Rattus rattus,first_detections,,1')
    expect(table).toContain('6,Rattus rattus,rate_per_100_camera_nights,2026-01,15.79')
    expect(table).toContain('4,,species_recorded,,0')
    expect(table).toContain('20,,camera_nights,2026-01,19')
    expect(table).toContain('20,,photos_reviewed,,5')
    expect(table).toContain('22,,reviewers,,2')
    expect(table.every(row => row.split(',').length === 5)).toBe(true)
  })
})
