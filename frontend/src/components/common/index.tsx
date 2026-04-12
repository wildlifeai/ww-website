import { useJob } from '../../hooks/useJob'

export function ErrorBanner({ error, onRetry }: { error: any; onRetry?: () => void }) {
  if (!error) return null

  return (
    <div style={{
      backgroundColor: 'var(--surface)',
      borderLeft: '4px solid var(--error)',
      padding: '1rem',
      margin: '1rem 0',
      borderRadius: 'var(--radius)'
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <h4 style={{ margin: 0, fontWeight: 500 }}>{error.message || 'An error occurred'}</h4>
          {error.details && <p style={{ margin: '0.5rem 0 0', fontSize: '0.875rem' }}>{error.details}</p>}
        </div>
        {error.retryable && onRetry && (
          <button className="btn" style={{ marginLeft: '1rem' }} onClick={onRetry}>
            Try again
          </button>
        )}
      </div>
    </div>
  )
}

export function JobProgress({ jobId }: { jobId: string | null }) {
  const { data: job, isLoading, error } = useJob(jobId)

  if (!jobId) return null
  if (isLoading) return <div>Loading job {jobId}...</div>
  if (error) return <ErrorBanner error={error} />
  if (!job) return null

  return (
    <div className="card" style={{ marginTop: '1rem' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.5rem' }}>
        <span style={{ textTransform: 'capitalize' }}>{job.status}</span>
        <span>{Math.round(job.progress * 100)}%</span>
      </div>
      <div style={{ 
        width: '100%', 
        height: '8px', 
        backgroundColor: 'var(--border)', 
        borderRadius: '4px',
        overflow: 'hidden'
      }}>
        <div style={{
          width: `${job.progress * 100}%`,
          height: '100%',
          backgroundColor: job.status === 'failed' ? 'var(--error)' : 'var(--primary)',
          transition: 'width 0.3s ease'
        }} />
      </div>

      {job.status === 'completed' && job.result_url && (
        <div style={{ marginTop: '1rem', textAlign: 'center' }}>
          <a href={job.result_url} className="btn" style={{ textDecoration: 'none', display: 'inline-block' }}>
            Download Result
          </a>
        </div>
      )}

      {job.status === 'failed' && (
        <ErrorBanner error={{ message: 'Job failed', details: job.error }} />
      )}
    </div>
  )
}
