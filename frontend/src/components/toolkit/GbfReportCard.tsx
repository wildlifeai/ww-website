// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// GbfReportCard: the GBF contribution report on the Toolkit page (#372), one tile per target for
// the projects picked in the header over a period, with CSV and JSON downloads. Lazy, and shown
// only while FF_GBF_REPORT_ENABLED is on. What each figure means is in the guide, not here.
import { useState, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Download, Leaf } from 'lucide-react'
import { Link } from 'react-router-dom'
import { apiClient } from '../../lib/apiClient'
import { saveFile } from '../../lib/camtrapExport'
import { gbifDatasetUrl } from '../../lib/gbifDataset'
import {
  GBF_CAVEAT, GBF_GUIDE, defaultPeriod, fetchGbfReport, gbfCsv, gbfFilename, periodProblem, speciesLabel, targetUrl,
  type GbfDataset, type GbfReport, type GbfSpecies, type GbfSpeciesTarget,
} from '../../lib/gbfReport'
import { useProjectSelection } from '../../hooks/useProjectSelection'
import { ToolCard } from './ToolCard'

const COUNT = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 })
const RATE = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 })
const n = (v: number) => COUNT.format(v)
const plural = (v: number, one: string, many: string) => `${n(v)} ${v === 1 ? one : many}`

const MUTED: React.CSSProperties = { margin: 0, fontSize: '0.8125rem', color: 'var(--text-muted)' }
const ERROR: React.CSSProperties = { margin: 0, fontSize: '0.8125rem', color: 'var(--error, #ef4444)' }
const LABEL: React.CSSProperties = { display: 'flex', flexDirection: 'column', gap: '0.25rem', fontSize: '0.8125rem', fontWeight: 500 }
const INPUT: React.CSSProperties = {
  padding: '0.35rem 0.5rem', borderRadius: 'var(--radius)', border: '1px solid var(--border)',
  backgroundColor: 'var(--surface)', color: 'var(--text-color)', fontSize: '0.8125rem',
}
const OUTLINE: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', gap: '0.35rem', padding: '0.35rem 0.75rem', fontSize: '0.8125rem',
  border: '1px solid var(--border)', borderRadius: 'var(--radius)', background: 'transparent', color: 'var(--text-color)', cursor: 'pointer',
}

function download(text: string, type: string, filename: string) {
  const url = URL.createObjectURL(new Blob([text], { type }))
  saveFile(url, filename)
  URL.revokeObjectURL(url)
}

export function GbfReportCard() {
  const { queryProjectIds, noProjectSelected } = useProjectSelection()
  const [period, setPeriod] = useState(() => defaultPeriod())
  const [asked, setAsked] = useState<{ ids: string[]; from: string; to: string } | null>(null)
  const problem = periodProblem(period.from, period.to)

  const report = useQuery({
    queryKey: ['gbf-report', asked],
    queryFn: () => (asked ? fetchGbfReport(apiClient.get, asked.ids, asked.from, asked.to) : Promise.reject(new Error('No report asked for'))),
    enabled: !!asked,
    staleTime: 10 * 60_000,
    retry: false,
  })

  const setDate = (k: 'from' | 'to') => (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value
    setPeriod(p => ({ ...p, [k]: value }))
  }

  return (
    <ToolCard
      icon={Leaf}
      title="GBF contribution"
      description="What the selected projects contribute to the Global Biodiversity Framework targets, for funders and councils."
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.875rem' }}>
        {noProjectSelected ? (
          <p style={MUTED}>Select a project in the header to report on it.</p>
        ) : (
          <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'flex-end', gap: '0.75rem' }}>
            <label style={LABEL}>From<input type="date" value={period.from} onChange={setDate('from')} style={INPUT} /></label>
            <label style={LABEL}>To<input type="date" value={period.to} onChange={setDate('to')} style={INPUT} /></label>
            <button
              className="btn"
              onClick={() => queryProjectIds && setAsked({ ids: queryProjectIds, ...period })}
              disabled={!!problem || !queryProjectIds || report.isFetching}
              style={{ fontSize: '0.8125rem', opacity: problem || !queryProjectIds ? 0.45 : 1 }}
            >
              {report.isFetching ? 'Calculating…' : 'Calculate'}
            </button>
          </div>
        )}
        {problem && <p style={ERROR}>{problem}</p>}
        {report.isError && <p style={ERROR}>{report.error.message}</p>}
        {report.data && <ReportTiles report={report.data} />}
        <p style={{ ...MUTED, fontSize: '0.75rem' }}>
          {GBF_CAVEAT} <Link to={GBF_GUIDE}>How it&apos;s calculated</Link>
        </p>
      </div>
    </ToolCard>
  )
}

function ReportTiles({ report }: { report: GbfReport }) {
  const e = report.target_20
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
      <p style={MUTED}>
        {report.projects.map(p => p.name ?? p.id).join(', ')}, {report.period.from} to {report.period.to}
      </p>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: '0.75rem' }}>
        <Tile target={6} title="Invasive species">
          <SpeciesFigures target={report.target_6} none="No invasive species detected." />
        </Tile>
        <Tile target={4} title="Threatened species">
          {report.target_4.species.length > 0 && <div>{n(report.target_4.species_recorded)} threatened species recorded</div>}
          <SpeciesFigures target={report.target_4} none="No threatened species detected." />
        </Tile>
        <Tile target={20} title="Monitoring effort">
          <div>{plural(e.cameras, 'camera', 'cameras')} at {plural(e.sites, 'site', 'sites')}</div>
          <div>{plural(e.camera_nights, 'camera-night', 'camera-nights')}</div>
          <div>{plural(e.photos, 'photo', 'photos')}: {n(e.photos_with_ai)} checked by AI, {n(e.photos_reviewed)} by people</div>
        </Tile>
        <Tile target={21} title="Open data">
          {report.target_21.datasets.map(d => (
            <OpenData key={d.project_id} dataset={d} named={report.target_21.datasets.length > 1} />
          ))}
        </Tile>
        <Tile target={22} title="Participation">
          <div>{plural(report.target_22.reviewers, 'person', 'people')} reviewed photos</div>
        </Tile>
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem' }}>
        <button type="button" style={OUTLINE} onClick={() => download(gbfCsv(report), 'text/csv', gbfFilename(report, 'csv'))}>
          <Download size={14} aria-hidden="true" />Download CSV
        </button>
        <button type="button" style={OUTLINE} onClick={() => download(JSON.stringify(report, null, 2), 'application/json', gbfFilename(report, 'json'))}>
          <Download size={14} aria-hidden="true" />Download JSON
        </button>
      </div>
    </div>
  )
}

function Tile({ target, title, children }: { target: number; title: string; children: ReactNode }) {
  return (
    <div style={{
      border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: '0.75rem',
      display: 'flex', flexDirection: 'column', gap: '0.375rem', fontSize: '0.8125rem',
    }}>
      <a href={targetUrl(target)} target="_blank" rel="noopener noreferrer" style={{ fontWeight: 600 }}>
        Target {target}: {title}
      </a>
      {children}
    </div>
  )
}

function SpeciesFigures({ target, none }: { target: GbfSpeciesTarget; none: string }) {
  if (target.species.length === 0) return <div style={{ color: 'var(--text-muted)' }}>{none}</div>
  return (
    <>
      {target.species.slice(0, 3).map(s => (
        <div key={s.taxon_id}>
          <div style={{ fontWeight: 500 }}>{speciesLabel(s)}</div>
          <div style={{ color: 'var(--text-muted)' }}>
            {s.rate === null ? plural(s.detections, 'detection', 'detections') : `${RATE.format(s.rate)} per 100 camera-nights`},
            {' '}at {n(s.sites_with)} of {plural(target.sites_monitored, 'site', 'sites')}
            {s.first_detections.length > 0 && `, new at ${plural(s.first_detections.length, 'site', 'sites')}`}
          </div>
          <Trend monthly={s.monthly} />
        </div>
      ))}
      {target.species.length > 3 && <div style={{ color: 'var(--text-muted)' }}>and {n(target.species.length - 3)} more in the download</div>}
    </>
  )
}

const DATASET_STATUS: Record<Exclude<GbfDataset['status'], 'published'>, string> = {
  not_published: 'Not published',
  not_found: 'GBIF no longer has the linked dataset',
  unavailable: 'GBIF did not answer. Try again later.',
}

function OpenData({ dataset: d, named }: { dataset: GbfDataset; named: boolean }) {
  return (
    <div>
      {named && <div style={{ fontWeight: 500 }}>{d.project_name ?? d.project_id}</div>}
      {d.status === 'published' && d.dataset_key ? (
        <>
          <a href={gbifDatasetUrl(d.dataset_key)} target="_blank" rel="noopener noreferrer">{d.title || 'GBIF dataset'}</a>
          <div>{plural(d.published_records ?? 0, 'record', 'records')} on GBIF{d.last_published && `, published ${d.last_published}`}</div>
        </>
      ) : (
        <div style={{ color: 'var(--text-muted)' }}>{DATASET_STATUS[d.status as keyof typeof DATASET_STATUS]}</div>
      )}
      <div style={{ color: 'var(--text-muted)' }}>{plural(d.reviewed_detections, 'reviewed detection', 'reviewed detections')} in the period</div>
    </div>
  )
}

/** The monthly rate as a small line, when the period spans more than one month. */
function Trend({ monthly }: { monthly: GbfSpecies['monthly'] }) {
  if (monthly.length < 2) return null
  const rates = monthly.map(m => m.rate ?? 0)
  const top = Math.max(...rates) || 1
  const points = rates.map((r, i) => `${(i / (rates.length - 1)) * 118 + 1},${23 - (r / top) * 22}`).join(' ')
  return (
    <svg width="120" height="24" role="img" aria-label="Monthly rate" style={{ display: 'block', marginTop: '0.25rem' }}>
      <polyline points={points} fill="none" stroke="var(--primary)" strokeWidth="1.5" />
    </svg>
  )
}
