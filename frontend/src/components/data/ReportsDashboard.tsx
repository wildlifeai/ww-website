/**
 * ReportsDashboard: the Insights > Reports counters and charts.
 *
 * A fixed set (UI audit decision 4): four counters, top species, observation types, and
 * observations per deployment, or detections per day by species when exactly one deployment is
 * in view (decision 5, which folds the Reporting page in). The database counts the observations
 * (ww-backend #334), lib/observationSummary sums its rows into each chart's own small table, and
 * the bar charts stop at 15 rows and say how many are left out. One deployment with no animal
 * observations shows its monitoring period and photo count instead of a bare zero, as Reporting
 * did.
 */
import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Camera, Download } from 'lucide-react'
import { VegaChart } from '../ui/VegaChart'
import { supabase } from '../../config/supabase'
import { useAuth } from '../../hooks/useAuth'
import { startDateLabel } from '../../lib/deploymentFilterOptions'
import { countDeploymentPhotos } from '../../lib/insightsReads'
import {
  capRows, detectionsPerDay, perDayCsv, summariseObservations, type DailyCount, type ObservationCount,
} from '../../lib/observationSummary'
import { countBarSpec, perDaySpec, typeArcSpec } from './chartSpec'

/** Bars per bar chart. */
const MAX_BARS = 15

const PANEL: React.CSSProperties = {
  background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: '1rem',
}
const MUTED: React.CSSProperties = { color: 'var(--text-muted)', fontSize: '0.8125rem' }
const SMALL_BTN: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', gap: '0.25rem', padding: '0.15rem 0.5rem', fontSize: '0.75rem',
  border: '1px solid var(--primary)', borderRadius: 'var(--radius)', background: 'transparent', color: 'var(--primary)', cursor: 'pointer',
}

/** The one deployment in view. */
export interface SingleDeployment {
  id: string
  timezone: string | null
  deployment_start: string | null
  deployment_end: string | null
}

interface Props {
  counts: ObservationCount[]
  /** Deployment id to its filter label, for the per-deployment chart. */
  deploymentLabels: ReadonlyMap<string, string>
  /** Set when exactly one deployment is in view: the per-day chart, by capture day in its zone. */
  singleDeployment?: SingleDeployment | null
  /** Its named observations per capture day; undefined while they load. */
  dailyCounts?: DailyCount[]
  /** The per-day read failed. */
  dailyFailed?: boolean
  /** A species filter is on, so a zero is about that species, not the deployment. */
  speciesFiltered?: boolean
  loading?: boolean
}

function downloadText(text: string, filename: string) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/csv' }))
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

export function ReportsDashboard({
  counts, deploymentLabels, singleDeployment, dailyCounts, dailyFailed, speciesFiltered, loading,
}: Props) {
  const summary = useMemo(() => summariseObservations(counts, deploymentLabels), [counts, deploymentLabels])
  const species = useMemo(() => capRows(summary.species, MAX_BARS), [summary])
  const perDeployment = useMemo(() => capRows(summary.deployments, MAX_BARS), [summary])
  const perDay = useMemo(() => (dailyCounts ? detectionsPerDay(dailyCounts) : null), [dailyCounts])

  const speciesSpec = useMemo(() => countBarSpec(species.shown, { title: 'Species', italic: true }), [species])
  const typesSpec = useMemo(() => typeArcSpec(summary.types), [summary])
  const perDeploymentSpec = useMemo(() => countBarSpec(perDeployment.shown, { title: 'Deployment' }), [perDeployment])
  const perDaySpecValue = useMemo(() => (perDay ? perDaySpec(perDay.rows, perDay.species) : null), [perDay])

  if (loading) {
    return <p style={{ ...MUTED, padding: '3rem', textAlign: 'center' }}>Loading observations…</p>
  }

  const counters: [string, number][] = [
    ['Total observations', summary.total],
    ['Unique species', summary.uniqueSpecies],
    ['Deployments with detections', summary.deploymentsWithDetections],
    ['Animal detections', summary.animals],
  ]
  const noAnimals = singleDeployment && !speciesFiltered && summary.animals === 0 ? singleDeployment : null

  // The CSV carries every species, not the eight the chart names.
  const downloadPerDay = () => {
    if (!singleDeployment || !dailyCounts) return
    const all = detectionsPerDay(dailyCounts, Number.MAX_SAFE_INTEGER)
    downloadText(perDayCsv(all.rows, all.species), `detections-per-day-${singleDeployment.id.slice(0, 8)}.csv`)
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: '1rem' }}>
        {counters.map(([label, value]) => (
          <div key={label} style={{ ...PANEL, textAlign: 'center' }}>
            <div style={{ fontSize: '1.875rem', fontWeight: 700, color: 'var(--primary)' }}>{value.toLocaleString()}</div>
            <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.25rem' }}>{label}</div>
          </div>
        ))}
      </div>

      {noAnimals && <NoAnimalsPanel deployment={noAnimals} />}

      {summary.total === 0 ? (
        !noAnimals && <p style={MUTED}>No observations for this selection.</p>
      ) : (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '1rem' }}>
            <ChartPanel title="Top species" shown={species.shown.length} more={species.more}>
              {species.shown.length > 0 ? <VegaChart spec={speciesSpec} /> : <p style={MUTED}>No named species.</p>}
            </ChartPanel>
            <ChartPanel title="Observation types">
              <VegaChart spec={typesSpec} />
            </ChartPanel>
          </div>
          {singleDeployment ? (
            <ChartPanel
              title="Detections per day"
              action={perDay && perDay.rows.length > 0 && (
                <button onClick={downloadPerDay} style={SMALL_BTN} title="Detections per day and species">
                  <Download size={12} aria-hidden />CSV
                </button>
              )}
            >
              <PerDayBody perDay={perDay} spec={perDaySpecValue} failed={dailyFailed} />
            </ChartPanel>
          ) : (
            <ChartPanel title="Observations per deployment" shown={perDeployment.shown.length} more={perDeployment.more}>
              <VegaChart spec={perDeploymentSpec} />
            </ChartPanel>
          )}
        </>
      )}
    </div>
  )
}

/** The per-day chart, with the detections it leaves out counted under it. */
function PerDayBody({ perDay, spec, failed }: {
  perDay: ReturnType<typeof detectionsPerDay> | null; spec: ReturnType<typeof perDaySpec> | null; failed?: boolean
}) {
  if (failed) return <p style={MUTED}>Detections per day are not available.</p>
  if (!perDay || !spec) return <p style={MUTED}>Loading…</p>
  return (
    <>
      {perDay.rows.length > 0
        ? <VegaChart spec={spec} />
        : perDay.undated === 0 && <p style={MUTED}>No named species.</p>}
      {perDay.undated > 0 && (
        <p style={{ ...MUTED, fontSize: '0.75rem', margin: '0.5rem 0 0' }}>
          {`Not shown: ${perDay.undated.toLocaleString()} detection${perDay.undated === 1 ? '' : 's'} without a capture time.`}
        </p>
      )}
    </>
  )
}

/** A chart card; with `more`, the heading says how many rows the chart shows of how many. */
function ChartPanel({ title, shown, more = 0, action, children }: {
  title: string; shown?: number; more?: number; action?: React.ReactNode; children: React.ReactNode
}) {
  return (
    <div style={PANEL}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.5rem', marginBottom: '0.5rem' }}>
        <span style={{ fontWeight: 600, fontSize: '0.875rem' }}>{title}</span>
        {more > 0 && shown != null && (
          <span style={{ ...MUTED, fontSize: '0.75rem', marginLeft: 'auto' }}>{`${shown} of ${shown + more}`}</span>
        )}
        {action && <span style={{ marginLeft: more > 0 ? undefined : 'auto' }}>{action}</span>}
      </div>
      {children}
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>{label}</div>
      <div style={{ fontWeight: 600 }}>{value}</div>
    </div>
  )
}

/**
 * One deployment with no animal observations: a real monitoring result, so it shows the period
 * and the photos behind the zero, and a way to check them in Annotations.
 */
function NoAnimalsPanel({ deployment }: { deployment: SingleDeployment }) {
  const { user } = useAuth()
  // Under ['insights', 'observations'], so an upload's phase change refreshes it too.
  const photos = useQuery({
    queryKey: ['insights', 'observations', 'photo-count', user?.id, deployment.id],
    queryFn: () => countDeploymentPhotos(supabase, deployment.id),
    enabled: !!user,
  })
  const start = startDateLabel(deployment.deployment_start, deployment.timezone)
  const end = startDateLabel(deployment.deployment_end, deployment.timezone)
  const photoCount = photos.isError ? 'not available' : photos.data == null ? '…' : photos.data.toLocaleString()
  return (
    <div style={{ ...PANEL, display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '1.5rem' }}>
      {start && <Stat label="Monitoring period" value={`${start} to ${end ?? 'ongoing'}`} />}
      <Stat label="Photos" value={photoCount} />
      <Stat label="Animal observations" value="0" />
      <Link
        to={`/annotations?deployment=${deployment.id}`}
        style={{ display: 'inline-flex', alignItems: 'center', gap: '0.375rem', marginLeft: 'auto', color: 'var(--primary)', fontSize: '0.8125rem', fontWeight: 600, textDecoration: 'none' }}
      >
        <Camera size={14} aria-hidden />{photos.data ? 'Review the photos in Media' : 'Open in Media'}
      </Link>
    </div>
  )
}
