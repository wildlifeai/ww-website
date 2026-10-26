import { useParams } from 'react-router-dom'
import { useDatasetHealth, useAlerts, useProjectQa } from '../hooks/useIntelligence'
import { FeatureOff } from '../components/common/FeatureOff'
import { isFeatureDisabled } from '../lib/featureFlags'
import { funnelLabel } from '../lib/reviewFunnel'

const SEVERITY_COLOR: Record<string, string> = { info: '#5b9cf6', warning: '#ff9800', critical: '#f44336' }

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="card" style={{ padding: '1rem 1.25rem', textAlign: 'center', flex: 1, minWidth: 120 }}>
      <div style={{ fontSize: '1.6rem', fontWeight: 'bold' }}>{value}</div>
      <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>{label}</div>
    </div>
  )
}

const MUTED: React.CSSProperties = { fontSize: '0.8125rem', color: 'var(--text-muted)' }

export function DatasetHealthPage() {
  const { project_id } = useParams<{ project_id: string }>()
  const { data: health, isLoading, error } = useDatasetHealth(project_id)
  const { data: alertsData } = useAlerts(project_id)
  // One QA call for the project, once the page itself answered (03 #19).
  const { data: qa, isLoading: qaLoading } = useProjectQa(project_id, !!health)

  if (isLoading) return <div style={{ padding: '2rem' }}>Loading dataset health…</div>
  if (isFeatureDisabled(error)) return <FeatureOff backTo="/" backLabel="Back to Home" />
  if (!health) return <div style={{ padding: '2rem', color: 'var(--error)' }}>Could not load dataset health{error ? `: ${error.message}` : '.'}</div>

  const funnelTotal = Object.values(health.review_funnel).reduce((a, b) => a + b, 0) || 1
  const alerts = alertsData?.alerts || []

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem', color: 'var(--text-color)' }}>
      <h3 style={{ margin: 0 }}>Dataset Health</h3>

      {/* Top stats */}
      <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
        <Stat label="Deployments" value={health.deployments} />
        <Stat label="Species" value={health.species_count} />
        <Stat label="Observations" value={health.total_observations} />
        <Stat label="Outlier rate" value={health.outlier_rate != null ? `${Math.round(health.outlier_rate * 100)}%` : 'n/a'} />
      </div>

      {/* Conservation alerts */}
      {alerts.length > 0 && (
        <div className="card" style={{ padding: '1.25rem' }}>
          <h4 style={{ marginTop: 0 }}>Conservation alerts ({alerts.length})</h4>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {alerts.map((a) => (
              <div key={a.id} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: '0.8125rem', padding: '0.5rem', background: 'rgba(0,0,0,0.15)', borderRadius: 'var(--radius)' }}>
                <span style={{ width: 8, height: 8, borderRadius: '50%', background: SEVERITY_COLOR[a.severity] || '#888' }} />
                <strong>{a.alert_type.replace(/_/g, ' ')}</strong>
                <span style={{ color: 'var(--text-muted)', fontSize: '0.7rem' }}>{a.severity}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Review funnel */}
      <div className="card" style={{ padding: '1.25rem' }}>
        <h4 style={{ marginTop: 0 }}>Review funnel</h4>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {Object.entries(health.review_funnel).map(([status, count]) => (
            <div key={status}>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                <span>{funnelLabel(status)}</span>
                <span>{count}</span>
              </div>
              <div style={{ height: 5, background: 'rgba(255,255,255,0.1)', borderRadius: 3, overflow: 'hidden' }}>
                <div style={{ height: '100%', width: `${(count / funnelTotal) * 100}%`, background: 'var(--primary)' }} />
              </div>
            </div>
          ))}
          {Object.keys(health.review_funnel).length === 0 && <span style={MUTED}>No observations yet.</span>}
        </div>
      </div>

      {/* AN-9: AI-vs-human agreement (QA) */}
      <div className="card" style={{ padding: '1.25rem' }}>
        <h4 style={{ marginTop: 0 }}>AI vs human agreement</h4>
        {qaLoading ? (
          <span style={MUTED}>Computing agreement…</span>
        ) : !qa || !qa.enabled ? (
          <span style={MUTED}>
            QA agreement is unavailable (the active-learning feature is disabled).
          </span>
        ) : qa.n_compared === 0 ? (
          <span style={MUTED}>
            No photo carries both an AI and a human label yet.
          </span>
        ) : (
          <>
            <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap', marginBottom: '0.75rem' }}>
              <Stat label="Agreement" value={qa.precision != null ? `${Math.round(qa.precision * 100)}%` : 'n/a'} />
              <Stat label="Images compared" value={qa.n_compared} />
              <Stat label="Matches" value={qa.matches} />
            </div>
            <p style={{ fontSize: '0.7rem', color: 'var(--text-muted)', margin: '0 0 0.75rem' }}>
              Share of photos where the human label matched the AI's, over photos carrying both.
            </p>
            {qa.perDeployment.length > 1 && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4, maxHeight: 220, overflowY: 'auto' }}>
                {qa.perDeployment.map(p => (
                  <div key={p.deployment_id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '0.8125rem', padding: '0.25rem 0', borderBottom: '1px solid var(--border)' }}>
                    <span>{p.location_name || p.deployment_id.slice(0, 8)}</span>
                    <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <span style={{ color: 'var(--text-muted)', fontSize: '0.7rem' }}>{p.matches}/{p.n_compared}</span>
                      <strong>{p.precision != null ? `${Math.round(p.precision * 100)}%` : 'n/a'}</strong>
                    </span>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>

      {/* Species coverage */}
      <div className="card" style={{ padding: '1.25rem' }}>
        <h4 style={{ marginTop: 0 }}>Species coverage</h4>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4, maxHeight: 360, overflowY: 'auto' }}>
          {health.species.map((s) => (
            <div key={s.scientific_name} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '0.8125rem', padding: '0.25rem 0', borderBottom: '1px solid var(--border)' }}>
              <span style={{ fontStyle: 'italic' }}>{s.scientific_name}</span>
              <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                {s.under_represented && <span title="< 10 observations" style={{ fontSize: '0.65rem', color: '#ff9800', border: '1px solid #ff9800', borderRadius: 10, padding: '0 6px' }}>low</span>}
                <strong>{s.count}</strong>
              </span>
            </div>
          ))}
          {health.species.length === 0 && <span style={MUTED}>No confirmed species yet.</span>}
        </div>
      </div>
    </div>
  )
}
