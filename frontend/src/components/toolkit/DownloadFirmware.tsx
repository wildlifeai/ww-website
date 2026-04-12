import { useState } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import { apiClient } from '../../lib/apiClient'
import { JobProgress } from '../common'

interface SSCMAModel {
  uuid: string
  name: string
  version: string
  category: string
  algorithm: string
  description: string
  classes: string[]
  image: string
  benchmark: Array<{
    backend: string
    precision: string
    device?: string[]
    metrics: Record<string, any>
    url: string
  }>
}

export function DownloadFirmware() {
  const [selectedModel, setSelectedModel] = useState<SSCMAModel | null>(null)
  const [jobId, setJobId] = useState<string | null>(null)

  const { data: catalogResponse, isLoading } = useQuery({
    queryKey: ['sscma-catalog'],
    queryFn: () => apiClient.get('/api/models/sscma/catalog'),
  })

  const catalog: SSCMAModel[] = catalogResponse?.data ?? []

  const downloadMutation = useMutation({
    mutationFn: (uuid: string) =>
      apiClient.post('/api/models/pretrained', { sscma_uuid: uuid }),
    onSuccess: (response: any) => {
      setJobId(response.data?.job_id)
    },
  })

  return (
    <div>
      <h3 style={{ marginBottom: '0.5rem' }}>Download Pre-trained Model</h3>
      <p style={{ opacity: 0.7, marginBottom: '1.5rem' }}>
        Browse the SSCMA Model Zoo and download a pre-trained model optimised for
        Wildlife Watcher hardware. Models with Vela support run directly on the
        Ethos-U55 NPU.
      </p>

      {isLoading && <p>Loading SSCMA catalog…</p>}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: '1rem' }}>
        {catalog.map((model) => {
          const hasVela = model.benchmark?.some((b) => b.backend === 'TFLite(vela)')
          return (
            <div
              key={model.uuid}
              className="card"
              style={{
                cursor: 'pointer',
                border: selectedModel?.uuid === model.uuid ? '2px solid var(--primary)' : '1px solid var(--border)',
                transition: 'border-color 0.2s, transform 0.15s',
              }}
              onClick={() => setSelectedModel(model)}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                <div>
                  <strong>{model.name}</strong>
                  <div style={{ fontSize: '0.75rem', opacity: 0.6 }}>{model.category} · {model.algorithm}</div>
                </div>
                {hasVela && (
                  <span style={{
                    fontSize: '0.625rem',
                    padding: '2px 6px',
                    borderRadius: '4px',
                    backgroundColor: 'var(--primary)',
                    color: 'white',
                    fontWeight: 600,
                  }}>
                    VELA
                  </span>
                )}
              </div>
              <p style={{ fontSize: '0.8125rem', marginTop: '0.5rem', opacity: 0.8 }}>
                {model.description?.slice(0, 120)}
              </p>
              <div style={{ fontSize: '0.75rem', opacity: 0.6, marginTop: '0.5rem' }}>
                Classes: {model.classes?.join(', ')}
              </div>
            </div>
          )
        })}
      </div>

      {selectedModel && (
        <div style={{ marginTop: '1.5rem', textAlign: 'center' }}>
          <button
            className="btn"
            disabled={downloadMutation.isPending}
            onClick={() => downloadMutation.mutate(selectedModel.uuid)}
            style={{ padding: '0.75rem 2rem' }}
          >
            {downloadMutation.isPending ? 'Submitting…' : `Download "${selectedModel.name}"`}
          </button>
        </div>
      )}

      {downloadMutation.isError && (
        <p style={{ color: 'var(--error)', marginTop: '1rem', textAlign: 'center' }}>
          {(downloadMutation.error as Error).message}
        </p>
      )}

      <JobProgress jobId={jobId} />
    </div>
  )
}
